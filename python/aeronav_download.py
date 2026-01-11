#!/usr/bin/env python3
'''
Standalone downloader for FAA Aeronav chart data.

Downloads current GeoTIFF chart ZIP files from aeronav.faa.gov.
Supports incremental updates via If-Modified-Since headers.

Usage:
    python aeronav_download.py <zippath>
    python aeronav_download.py --zippath /path/to/zips
    python aeronav_download.py --zippath /path/to/zips --quiet
'''

import argparse
import os
import re
import time
import urllib.error
import urllib.request

import bs4

# FAA Aeronav index URLs
_AERONAV_VFR_URL = 'https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/vfr/'
_AERONAV_IFR_URL = 'https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/ifr/'

# Chart type IDs used on the FAA website (div IDs in the HTML)
_VFR_CHART_TYPES = ['sectional', 'terminalArea', 'helicopter', 'grandCanyon', 'Planning', 'caribbean']
_IFR_CHART_TYPES = ['lowsHighsAreas', 'planning', 'caribbean', 'gulf']


def download(url, path='.', filename=None):
    '''
    Downloads a file from the given URL and saves it to the specified path and filename.
    If the filename is not provided, the file will be saved with the basename of the URL.
    If the file already exists, the function will add an 'If-Modified-Since' header to the request
    to avoid downloading the file again if it has not been modified.

    Args:
        url (str): The URL of the file to download.
        path (str, optional): The directory path to save the file. Defaults to '.'.
        filename (str, optional): The name of the file to save. Defaults to None (uses basename of URL).

    Returns:
        str: The full path of the downloaded file.

    Raises:
        urllib.error.HTTPError: If an HTTP error occurs other than a 304 Not Modified response.
    '''
    # Create a request to retrieve the file
    request = urllib.request.Request(url)

    # Set filename
    filename = os.path.join(path, filename or os.path.basename(url))

    # Check if the file already exists on the filesystem and add the If-Modified-Since header to the request
    if os.path.exists(filename):
        last_modified_time = os.path.getmtime(filename)
        last_modified_time_str = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(last_modified_time))
        request.add_header('If-Modified-Since', last_modified_time_str)

    # Download the file
    try:
        with urllib.request.urlopen(request) as response:
            if response.status == 200:
                with open(filename, 'wb') as f:
                    f.write(response.read())

    except urllib.error.HTTPError as e:
        # Check if the server returned a 304 Not Modified response, if so, just skip the download
        if e.code != 304:
            raise

    return filename


def get_current_aeronav_urls(index_url, chart_types):
    '''
    Scrapes the aeronav.faa.gov website for the current URLs of the specified chart types.

    Args:
        index_url (str): The URL of the index page to scrape.
        chart_types (list): List of chart type div IDs to look for on the page.

    Returns:
        list: List of GeoTIFF ZIP file URLs found on the page.
    '''
    # Read the page for scraping
    with urllib.request.urlopen(index_url) as response:
        html = response.read().decode('utf-8')

    soup = bs4.BeautifulSoup(html, 'html.parser')

    urls = []
    for chart_type in chart_types:
        # Each chart type has its own div
        chart = soup.find('div', id=chart_type)
        if chart:
            # Any table describes a set of charts
            tables = chart.find_all('table')
            for table in tables:
                # Each row in the table describes a chart
                rows = table.find_all('tr')
                for row in rows:
                    # The second cell in each row contains the current chart info
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        # Find all <a> elements in the cell
                        a_tags = cells[1].find_all('a')
                        for a_tag in a_tags:
                            # Check if the link text is 'Geo-TIFF'
                            if a_tag and a_tag.get_text(strip=True).lower() == 'geo-tiff':
                                # Finally, there it is
                                geo_tiff_url = a_tag['href']
                                urls.append(geo_tiff_url)
    return urls


def fix_faa_incorrect_urls(urls):
    '''
    Fixes incorrect URLs on the aeronav.faa.gov website by finding the most common "before date" part of the URL
    and using that as the base URL for all URLs. This is necessary because the FAA's web site doesn't always
    link to the correct file. FAA, get your shit together.

    Args:
        urls (list): List of URLs to fix.

    Returns:
        list: List of corrected URLs with consistent date paths.
    '''
    url_tuples = []
    date_pattern = re.compile(r'/(\d{2}-\d{2}-\d{4})/')

    # Examine each URL, and find the date in the URL
    for url in urls:
        match = date_pattern.search(url)
        if match:
            # Store everything up to and including the date, then everything after the date
            before_date = url[:match.end(1)]
            after_date = url[match.end(1):]
            url_tuples.append((before_date, after_date))
        else:
            raise ValueError(f"Could not find date in aeronav URL {url}")

    # All "before date" parts should be the same. Tally up the counts of each "before date" part
    baseurl_counts = {}
    for before_date, _ in url_tuples:
        if before_date not in baseurl_counts:
            baseurl_counts[before_date] = 0
        baseurl_counts[before_date] += 1

    # If they are not all the same, use the most common one
    if len(baseurl_counts) > 1:
        most_common_baseurl = max(baseurl_counts, key=lambda x: baseurl_counts[x])
        cleaned_urls = []
        for before_date, after_date in url_tuples:
            if before_date != most_common_baseurl:
                cleaned_urls.append(most_common_baseurl + after_date)
            else:
                cleaned_urls.append(before_date + after_date)
        return cleaned_urls
    else:
        return urls


def main():
    '''
    Main entry point for the standalone downloader.
    '''
    parser = argparse.ArgumentParser(
        description='Download FAA Aeronav chart data from aeronav.faa.gov.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
    %(prog)s /path/to/zips
    %(prog)s --zippath /path/to/zips
    %(prog)s --zippath /path/to/zips --quiet
'''
    )
    parser.add_argument('zippath', nargs='?', help='Directory to store downloaded ZIP files.')
    parser.add_argument('--zippath', dest='zippath_opt', help='Directory to store downloaded ZIP files (alternative to positional argument).')
    parser.add_argument('--quiet', action='store_true', help='Suppress output.')
    args = parser.parse_args()

    # Allow either positional or --zippath argument
    zippath = args.zippath or args.zippath_opt
    if not zippath:
        parser.error('zippath is required (either as positional argument or --zippath)')

    quiet = args.quiet

    if not quiet:
        print('Scraping aeronav.faa.gov...')

    # Scrape all chart file URLs
    vfr_urls = get_current_aeronav_urls(_AERONAV_VFR_URL, _VFR_CHART_TYPES)
    ifr_urls = get_current_aeronav_urls(_AERONAV_IFR_URL, _IFR_CHART_TYPES)

    # aeronav.faa.gov may have some incorrect URLs, so we need to clean them up
    vfr_urls = fix_faa_incorrect_urls(vfr_urls)
    ifr_urls = fix_faa_incorrect_urls(ifr_urls)

    all_urls = vfr_urls + ifr_urls

    if not quiet:
        print(f'Found {len(all_urls)} chart files to download.')

    # Create the directory if it does not exist
    os.makedirs(zippath, exist_ok=True)

    # Download all the files
    downloaded = 0
    skipped = 0
    for url in all_urls:
        filename = os.path.basename(url)
        filepath = os.path.join(zippath, filename)
        existed = os.path.exists(filepath)
        old_mtime = os.path.getmtime(filepath) if existed else 0

        if not quiet:
            print(f'Downloading {filename}...', end=' ', flush=True)

        download(url, zippath)

        # Check if file was actually downloaded or skipped
        new_mtime = os.path.getmtime(filepath)
        if existed and new_mtime == old_mtime:
            skipped += 1
            if not quiet:
                print('(not modified)')
        else:
            downloaded += 1
            if not quiet:
                print('done')

    if not quiet:
        print(f'\nDownload complete: {downloaded} downloaded, {skipped} already up to date.')


if __name__ == '__main__':
    main()
