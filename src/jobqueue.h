/*
 * jobqueue - Generic parallel job queue
 *
 * Manages a pool of worker processes that execute jobs from a queue.
 * Jobs are processed in parallel with at most max_workers concurrent jobs.
 * When a worker finishes a job, it is assigned the next pending job.
 */

#ifndef JOBQUEUE_H
#define JOBQUEUE_H

#include <stdbool.h>

/* Maximum number of concurrent workers */
#define JQ_MAX_WORKERS 64

/*
 * Job execution function type.
 * Called by worker processes to execute a job.
 *
 * Parameters:
 *   job_index   - Index of the job to execute
 *   job_data    - Opaque pointer to job data array (cast to appropriate type)
 *
 * Returns 0 on success, non-zero on failure.
 */
typedef int (*job_func_t)(int job_index, void *job_data);

/*
 * Worker initialization function type.
 * Called once per worker after fork to initialize worker-specific resources.
 *
 * Parameters:
 *   worker_id   - Worker identifier (0 to max_workers-1)
 *   init_data   - Opaque pointer to initialization data
 *
 * Returns 0 on success, non-zero on failure.
 */
typedef int (*worker_init_t)(int worker_id, void *init_data);

/*
 * Job queue configuration.
 */
typedef struct {
    int num_jobs;              /* Total number of jobs */
    int max_workers;           /* Maximum concurrent workers */
    void *job_data;            /* Opaque pointer to job data array */
    job_func_t job_func;       /* Function to execute each job */
    worker_init_t worker_init; /* Optional: worker initialization function */
    void *init_data;           /* Optional: data passed to worker_init */
    const char **job_names;    /* Optional: job names for progress display */
} JobQueueConfig;

/*
 * Job queue result statistics.
 */
typedef struct {
    int completed; /* Number of successfully completed jobs */
    int failed;    /* Number of failed jobs */
} JobQueueResult;

/*
 * Execute all jobs in the queue using parallel workers.
 *
 * Forks max_workers worker processes, each of which:
 * 1. Calls worker_init (if provided) to initialize resources
 * 2. Receives job indices from parent, executes them via job_func
 * 3. Signals completion status back to parent
 *
 * The parent process manages the queue, dispatching jobs to idle workers
 * until all jobs are complete.
 *
 * Parameters:
 *   config  - Job queue configuration
 *   result  - Output: job execution statistics
 *
 * Returns 0 on success (all jobs completed), -1 on error.
 */
int jobqueue_run(const JobQueueConfig *config, JobQueueResult *result);

#endif /* JOBQUEUE_H */
