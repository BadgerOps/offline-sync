import os
import urllib.request
import gzip
import xml.etree.ElementTree as ET
import configparser
import argparse
import logging
import time


def setup_logging(log_file='epel_manager.log'):
    # Create a logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Create a formatter
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # Create a file handler and set level to info
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # If running in an IDE, also log to stdout
    if 'PYTHON_IDE' in os.environ:
        stdout_handler = logging.StreamHandler()
        stdout_handler.setFormatter(formatter)
        logger.addHandler(stdout_handler)


def load_config(config_file='./config.ini'):
    logging.debug("loading configuration data from config.ini")
    config = configparser.ConfigParser()
    config.read(config_file)
    return config




class EPelpackageManager:
    def __init__(self, base_url, local_dir):
        self.base_url = base_url
        self.local_dir = local_dir
        os.makedirs(self.local_dir, exist_ok=True)
        self.parse_arguments()
        logging.info(f"Initialized EPelpackageManager for {base_url} with local dir {local_dir}")


    def parse_arguments(self):
        parser = argparse.ArgumentParser(description="Download packages from EPEL")
        parser.add_argument('-f', '--force', action='store_true', help="Force re-download of everything")
        self.args = parser.parse_args()

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

        with urllib.request.urlopen(file_url) as response, open(filepath, 'wb') as out_file:
            out_file.write(response.read())

    def enumerate_and_download_packages(self):
        """

        :return:
        """
        start_time = time.time()
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
        for pkg in packages:
            pkg_location = pkg.find("common:location", namespaces=package_namespace).get('href')
            pkg_url = os.path.join(self.base_url, pkg_location)
            logging.debug(f"checking package {pkg_url}")
            if pkg_url.split('/')[-2] == 't':
                if self.args.force or self.file_needs_update(pkg_url):
                    logging.debug(f"Downloading package: {pkg_url}")
                    self.download_file(pkg_url)



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

    def file_needs_update(self, file_url):
        """Check if the file needs to be updated."""
        local_path = self.get_local_path(file_url)

        if not os.path.exists(local_path):
            return True  # file does not exist locally

        # If you're using file sizes to check, get remote file size
        remote_file_info = urllib.request.urlopen(file_url).info()
        remote_size = int(remote_file_info.get('Content-Length', 0))

        local_size = os.path.getsize(local_path)

        if local_size != remote_size:
            return True

        return False

if __name__ == "__main__":
    setup_logging()
    config = load_config()
    for version in config.sections():
        logging.info(f"Processing {version}...")
        base_url = config[version]['base_url']
        local_dir = config[version]['local_dir']
        manager = EPelpackageManager(base_url, local_dir)
        manager.enumerate_and_download_packages()
        if not manager.args.force:
            manager.check_for_updates()

