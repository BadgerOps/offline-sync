#!/usr/bin/env python3.9

# we lock to python 3.9 for now...

import os
import urllib.request
import gzip
import xml.etree.ElementTree as ET
import configparser
import argparse
import logging
import time
import threading
import hashlib
from datetime import datetime
import concurrent.futures


def load_config(config_file='./config.ini'):
    logging.debug("loading configuration data from config.ini")
    config = configparser.ConfigParser()
    config.read(config_file)
    return config


class EPELDownloader:
    def __init__(self, base_url, local_dir):
        self.base_url = base_url
        self.local_dir = local_dir
        os.makedirs(self.local_dir, exist_ok=True)
        self.parse_arguments()
        self.num_threads = 6
        self.setup_logging()
        self._status = {'status': 'init',
                        'threadstatus': {},
                        }
        logging.info(f"Initialized EPELDownloader for {base_url} with local dir {local_dir}")

    def setup_logging(self, log_file='epel_manager.log'):
        # Create a logger
        logger = logging.getLogger()
        if self.args.debug:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)

        # Create a formatter
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

        # Create a file handler and set level to info
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        if self.args.debug:
            stdout_handler = logging.StreamHandler()
            logger.addHandler(stdout_handler)

        # If running in an IDE, also log to stdout
        if 'PYTHON_IDE' in os.environ:
            stdout_handler = logging.StreamHandler()
            stdout_handler.setFormatter(formatter)
            logger.addHandler(stdout_handler)

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description="Download packages from EPEL")
        parser.add_argument('-f', '--force', action='store_true', help="Force re-download of everything")
        parser.add_argument('-d', '--debug', action='store_true', help="Turn on debug options")
        self.args = parser.parse_args()

    def status(self):
        """Make a copy of status dict and return it"""
        currentstatus = self._status.copy()
        return currentstatus

    def set_threadstatus(self, thread, status):
        if not thread in self._status['threadstatus'].keys():
            self._status['threadstatus'][thread] = {}
        self._status['threadstatus'][thread]['ts'] = datetime.now()
        self._status['threadstatus'][thread]['status'] = str(status)
        logging.debug("Status: {1}".format(thread, status))


    def fetch_xml(self, url):
        logging.info(f"Fetching XML from {url}")
        with urllib.request.urlopen(url) as response:
            return ET.parse(response)

    def get_repomd_path(self):
        primary_xml_path = os.path.join(self.base_url, 'repodata/repomd.xml')
        return primary_xml_path

    def fetch_and_extract_gz(self, url, extraction_path):
        with urllib.request.urlopen(url) as response:
            with gzip.GzipFile(fileobj=response) as uncompressed:
                with open(extraction_path, 'wb') as outfile:
                    outfile.write(uncompressed.read())

    def download_file(self, file_url):
        logging.debug(f"Downloading file {file_url}")
        '''
        Download the files...
        '''
        filename = file_url.split('/')[-1]
        filepath = os.path.join(self.local_dir, file_url.replace(self.base_url, ''))
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        logging.debug(f"Updating package: {filepath}")
        with urllib.request.urlopen(file_url) as response, open(filepath, 'wb') as out_file:
            out_file.write(response.read())

    def download_package_group(self, package_group):
        thread_name = threading.current_thread().name
        logging.info(
        f"{thread_name} started processing {len(package_group)} packages")
        for pkg_location in package_group:
            pkg_url = os.path.join(self.base_url, pkg_location)
            if self.args.force or self.file_needs_update(pkg_url, pkg_location):
                self.download_file(pkg_url)
            else:
                logging.debug(f"Not downloading package: {pkg_location} since it is up to date")
        logging.info(f"{thread_name} finished processing packages")

    def enumerate_and_download_packages(self):
        """
        TODO: break this mess up (haha, as with every project)
        :return:
        """
        start_time = time.time()
        self.package_checksums = {}
        logging.info(f"Downloading repomd.xml from {self.base_url}")
        repomd_path = self.get_repomd_path()
        self.download_file(repomd_path)
        tree = self.fetch_xml(repomd_path)
        root = tree.getroot()

        namespace = {'repo': 'http://linux.duke.edu/metadata/repo'}
        primary_xml_location = root.find("repo:data[@type='primary']/repo:location", namespaces=namespace).get('href')

        primary_xml_url = os.path.join(self.base_url, primary_xml_location)
        primary_xml_local_path = os.path.join(self.local_dir, "primary.xml")

        self.fetch_and_extract_gz(primary_xml_url, primary_xml_local_path)

        primary_tree = ET.parse(primary_xml_local_path)
        primary_root = primary_tree.getroot()

        logging.debug(f"Getting all repomd files...")
        for data in root.findall('{http://linux.duke.edu/metadata/repo}data'):
            if data.find('{http://linux.duke.edu/metadata/repo}location') is not None:
                href = data.find('{http://linux.duke.edu/metadata/repo}location').get('href')
                file_url = os.path.join(self.base_url, href)
                self.download_file(file_url)
            else:
                logging.debug(f"Already downloaded file ")

        package_namespace = {'common': 'http://linux.duke.edu/metadata/common'}
        packages = primary_root.findall("common:package", namespaces=package_namespace)
        grouped_packages = {}
        for pkg in packages:
            pkg_location = pkg.find("common:location", namespaces=package_namespace).get('href')
            checksum_elem = pkg.find("common:checksum[@type='sha256']", namespaces=package_namespace)
            if checksum_elem is not None:
                self.package_checksums[pkg_location] = checksum_elem.text
            start_letter = pkg_location.split('/')[1]  # Get the starting letter
            if start_letter not in grouped_packages:
                grouped_packages[start_letter] = []
            grouped_packages[start_letter].append(pkg_location)

        # Use ThreadPoolExecutor to download packages in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            # Using a list comprehension to start all threads
            futures = [executor.submit(self.download_package_group, package_group) for package_group in
                       grouped_packages.values()]

            # Wait for all threads to complete
            for future in concurrent.futures.as_completed(futures):
                future.result()

        elapsed_time = time.time() - start_time
        logging.info(f"Downloaded repo from {self.base_url} in {elapsed_time:.2f} seconds.")

        total_size = 0
        for root, dirs, files in os.walk(self.local_dir):
            for file in files:
                total_size += os.path.getsize(os.path.join(root, file))
        logging.info(f"Total space used by the repo: {total_size / (1024 * 1024):.2f} MB")

    def check_for_updates(self):
        # Compare local repomd.xml with remote one
        local_repomd_path = os.path.join(self.local_dir, "repodata/repomd.xml")
        if not os.path.exists(local_repomd_path):
            logging.info(f"Local repomd.xml does not exist. Running the enumeration and download method first.")
            self.enumerate_and_download_packages()

        local_repomd_tree = ET.parse(local_repomd_path)
        local_date = local_repomd_tree.find(".//{http://linux.duke.edu/metadata/repo}revision").text

        remote_repomd_tree = self.fetch_xml(os.path.join(self.base_url, "repodata/repomd.xml"))
        remote_date = remote_repomd_tree.find(".//{http://linux.duke.edu/metadata/repo}revision").text

        if local_date != remote_date:
            logging.info(f"Updates are available. Consider rerunning the enumeration and download method.")
        else:
            logging.info(f"No updates are available.")

    def get_local_path(self, file_url):
        """Given a file URL, return its local path."""
        return os.path.join(self.local_dir, file_url.replace(self.base_url, ''))

    def file_needs_update(self, file_url, pkg_location):
        """Check if the file needs to be updated."""
        local_path = self.get_local_path(file_url)

        if not os.path.exists(local_path):
            return True  # file does not exist locally

        # Compute local file's SHA256 checksum
        with open(local_path, 'rb') as f:
            local_checksum = hashlib.sha256(f.read()).hexdigest()
        # Compare with remote checksum
        remote_checksum = self.package_checksums.get(pkg_location)
        if remote_checksum and local_checksum != remote_checksum:
            logging.debug(f"file size changed, re-downloading")
            return True
        logging.debug(f"Local file hash matches, no need to re-download {file_url}")
        return False

if __name__ == "__main__":
    config = load_config()
    for version in config.sections():
        logging.info(f"Processing {version}...")
        base_url = config[version]['base_url']
        local_dir = config[version]['local_dir']
        manager = EPELDownloader(base_url, local_dir)
        manager.check_for_updates()
        manager.enumerate_and_download_packages()

