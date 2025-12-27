/*
 * jobqueue - Generic parallel job queue implementation
 *
 * Uses fork() and pipes for IPC between parent and worker processes.
 * Parent dispatches jobs to idle workers; workers signal completion.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
#include <poll.h>
#include <errno.h>

#include "jobqueue.h"

/* Simple error logging macro */
#define JQ_ERROR(fmt, ...) fprintf(stderr, "jobqueue: " fmt "\n", ##__VA_ARGS__)

/* Message types for IPC */
typedef struct {
    int job_index;    /* Job index, or -1 for "shutdown" */
} JobMessage;

typedef struct {
    int job_index;    /* Job that completed */
    int status;       /* 0 = success, non-zero = failure */
} StatusMessage;

/* Per-worker state */
typedef struct {
    pid_t pid;
    int to_worker[2];    /* Pipe: parent writes, worker reads */
    int from_worker[2];  /* Pipe: worker writes, parent reads */
    int current_job;     /* Job currently assigned, or -1 if idle */
    bool active;         /* True if worker is running */
} WorkerState;

/*
 * Worker process main loop.
 */
static void worker_loop(int worker_id,
                        int read_fd, int write_fd,
                        const JobQueueConfig *config) {
    /* Initialize worker resources */
    if (config->worker_init) {
        if (config->worker_init(worker_id, config->init_data) != 0) {
            JQ_ERROR("worker %d: initialization failed", worker_id);
            _exit(1);
        }
    }

    /* Process jobs until shutdown */
    JobMessage msg;
    StatusMessage status;

    while (1) {
        /* Read job index from parent */
        ssize_t n = read(read_fd, &msg, sizeof(msg));
        if (n <= 0) {
            /* Parent closed pipe or error - exit */
            break;
        }

        if (msg.job_index < 0) {
            /* Shutdown signal */
            break;
        }

        /* Execute the job */
        status.job_index = msg.job_index;
        status.status = config->job_func(msg.job_index, config->job_data);

        /* Send completion status to parent */
        if (write(write_fd, &status, sizeof(status)) != sizeof(status)) {
            JQ_ERROR("worker %d: failed to write status", worker_id);
            break;
        }
    }

    close(read_fd);
    close(write_fd);
    _exit(0);
}

/*
 * Send a job to a worker.
 */
static int send_job(WorkerState *worker, int job_index) {
    JobMessage msg = { .job_index = job_index };
    if (write(worker->to_worker[1], &msg, sizeof(msg)) != sizeof(msg)) {
        return -1;
    }
    worker->current_job = job_index;
    return 0;
}

/*
 * Receive status from a worker.
 */
static int receive_status(WorkerState *worker, StatusMessage *status) {
    ssize_t n = read(worker->from_worker[0], status, sizeof(*status));
    if (n != sizeof(*status)) {
        return -1;
    }
    worker->current_job = -1;
    return 0;
}

int jobqueue_run(const JobQueueConfig *config, JobQueueResult *result) {
    if (!config || !config->job_func || config->num_jobs < 0) {
        JQ_ERROR("invalid config");
        return -1;
    }

    /* Initialize result */
    if (result) {
        result->completed = 0;
        result->failed = 0;
    }

    /* Handle edge case: no jobs */
    if (config->num_jobs == 0) {
        return 0;
    }

    /* Determine actual number of workers */
    int num_workers = config->max_workers;
    if (num_workers > config->num_jobs) {
        num_workers = config->num_jobs;
    }
    if (num_workers > JQ_MAX_WORKERS) {
        num_workers = JQ_MAX_WORKERS;
    }
    if (num_workers < 1) {
        num_workers = 1;
    }

    /* Allocate worker state */
    WorkerState *workers = calloc(num_workers, sizeof(WorkerState));
    if (!workers) {
        JQ_ERROR("failed to allocate worker state");
        return -1;
    }

    /* Initialize workers */
    for (int i = 0; i < num_workers; i++) {
        workers[i].pid = -1;
        workers[i].current_job = -1;
        workers[i].active = false;
    }

    int ret = -1;
    int started_workers = 0;

    /* Create worker processes */
    for (int i = 0; i < num_workers; i++) {
        /* Create pipes */
        if (pipe(workers[i].to_worker) < 0 || pipe(workers[i].from_worker) < 0) {
            JQ_ERROR("failed to create pipes for worker %d", i);
            goto cleanup;
        }

        pid_t pid = fork();
        if (pid < 0) {
            JQ_ERROR("failed to fork worker %d", i);
            close(workers[i].to_worker[0]);
            close(workers[i].to_worker[1]);
            close(workers[i].from_worker[0]);
            close(workers[i].from_worker[1]);
            goto cleanup;
        }

        if (pid == 0) {
            /* Child: close parent ends of pipes */
            close(workers[i].to_worker[1]);
            close(workers[i].from_worker[0]);

            /* Close pipes for other workers */
            for (int j = 0; j < i; j++) {
                close(workers[j].to_worker[1]);
                close(workers[j].from_worker[0]);
            }

            worker_loop(i, workers[i].to_worker[0], workers[i].from_worker[1], config);
            /* worker_loop doesn't return */
        }

        /* Parent: close child ends of pipes */
        close(workers[i].to_worker[0]);
        close(workers[i].from_worker[1]);

        workers[i].pid = pid;
        workers[i].active = true;
        started_workers++;
    }

    /* Job queue state */
    int next_job = 0;                /* Next job to assign */
    int jobs_completed = 0;          /* Total completed (success + failure) */
    int jobs_succeeded = 0;
    int jobs_failed = 0;

    /* Set up poll structure for all worker response pipes */
    struct pollfd *fds = malloc(num_workers * sizeof(struct pollfd));
    if (!fds) {
        JQ_ERROR("failed to allocate poll fds");
        goto cleanup;
    }

    for (int i = 0; i < num_workers; i++) {
        fds[i].fd = workers[i].from_worker[0];
        fds[i].events = POLLIN;
    }

    /* Initial job distribution - assign one job to each worker */
    for (int i = 0; i < num_workers && next_job < config->num_jobs; i++) {
        if (send_job(&workers[i], next_job) < 0) {
            JQ_ERROR("failed to send initial job to worker %d", i);
            workers[i].active = false;
        } else {
            next_job++;
        }
    }

    /* Main dispatch loop */
    while (jobs_completed < config->num_jobs) {
        /* Count active workers */
        int active_count = 0;
        for (int i = 0; i < num_workers; i++) {
            if (workers[i].active) {
                active_count++;
            }
        }

        if (active_count == 0) {
            JQ_ERROR("all workers died");
            break;
        }

        /* Wait for any worker to respond */
        int ready = poll(fds, num_workers, -1);
        if (ready < 0) {
            if (errno == EINTR) continue;
            JQ_ERROR("poll failed");
            break;
        }

        /* Check each worker for responses */
        for (int i = 0; i < num_workers; i++) {
            if (!workers[i].active) continue;

            if (fds[i].revents & POLLIN) {
                StatusMessage status;
                if (receive_status(&workers[i], &status) < 0) {
                    JQ_ERROR("failed to receive status from worker %d", i);
                    workers[i].active = false;
                    continue;
                }

                /* Record result */
                jobs_completed++;
                if (status.status == 0) {
                    jobs_succeeded++;
                } else {
                    jobs_failed++;
                }

                /* Assign next job if available */
                if (next_job < config->num_jobs) {
                    if (send_job(&workers[i], next_job) < 0) {
                        JQ_ERROR("failed to send job to worker %d", i);
                        workers[i].active = false;
                    } else {
                        next_job++;
                    }
                }
            }

            if (fds[i].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                /* Worker pipe closed unexpectedly */
                if (workers[i].current_job >= 0) {
                    jobs_completed++;
                    jobs_failed++;
                }
                workers[i].active = false;
            }
        }
    }

    free(fds);

    /* All jobs processed */
    ret = (jobs_failed == 0) ? 0 : -1;

    if (result) {
        result->completed = jobs_succeeded;
        result->failed = jobs_failed;
    }

cleanup:
    /* Shutdown all workers */
    JobMessage shutdown_msg = { .job_index = -1 };
    for (int i = 0; i < num_workers; i++) {
        if (workers[i].pid > 0) {
            /* Send shutdown message (ignore errors during cleanup) */
            if (write(workers[i].to_worker[1], &shutdown_msg, sizeof(shutdown_msg)) < 0) {
                /* Worker may have already exited */
            }
            close(workers[i].to_worker[1]);
            close(workers[i].from_worker[0]);
        }
    }

    /* Wait for all workers to exit */
    for (int i = 0; i < num_workers; i++) {
        if (workers[i].pid > 0) {
            int status;
            waitpid(workers[i].pid, &status, 0);
        }
    }

    free(workers);
    return ret;
}
