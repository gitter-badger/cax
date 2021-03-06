
import logging
from json import loads

import pymongo
from bson.json_util import dumps

from cax import config


class Task:
    def __init__(self):
        # Grab the Run DB so we can query it
        self.collection = config.mongo_collection()
        self.log = logging.getLogger(self.__class__.__name__)
        self.run_doc = None
        self.untriggered_data = None

    def go(self, specify_run = None):
        """Run this periodically"""

        query = {}

        # argument can be run number or run name
        if specify_run is not None:
            if isinstance(specify_run,int):
                query['number'] = specify_run
            elif isinstance(specify_run,str):
                query['name'] = specify_run

        # Get user-specified list of datasets
        datasets = config.get_dataset_list()

        # Collect all run document ids.  This has to be turned into a list
        # to avoid timeouts if a task takes too long.
        try:
            ids = [doc['_id'] for doc in self.collection.find(query,
                                                              projection=('_id'),
                                                              sort=(('start', -1),))]
        except pymongo.errors.CursorNotFound:
            self.log.warning("Cursor not found exception.  Skipping")
            return

        # Iterate over each run
        for id in ids:
            # Make sure up to date
            try:
                self.run_doc = self.collection.find_one({'_id': id})
            except pymongo.errors.AutoReconnect:
                self.log.error("pymongo.errors.AutoReconnect, skipping...")
                continue

            if 'data' not in self.run_doc:
                continue

            # Operate on only user-specified datasets
            if datasets:
                if self.run_doc['name'] not in datasets:
                    continue

            # DAQ experts only:
            # Find location of untriggered DAQ data (if exists)
            self.untriggered_data = self.get_daq_buffer()

            self.each_run()

        self.shutdown()

    def each_run(self):
        for data_doc in self.run_doc['data']:
            self.log.debug('%s on %s %s' % (self.__class__.__name__,
                                            self.run_doc['number'],
                                            data_doc['host']))

            self.each_location(data_doc)

    def each_location(self, data_doc):
        raise NotImplementedError()

    def get_daq_buffer(self):
        for data_doc in self.run_doc['data']:
            if data_doc['type'] == 'untriggered':
                if data_doc['host'] == 'reader':
                    if config.get_hostname() == 'eb0':
                        return data_doc

        # Not found
        return None

    def give_error(self, message):
        """Report error to PagerDuty and log

        This calls peoples and issues a wide range of alarms, so use wisely.
        """
        santized_run_doc = self.run_doc.copy()
        santized_run_doc = loads(dumps(santized_run_doc))

        self.log.error(message)

    def has_tag(self, name):
        if 'tags' not in self.run_doc:
            return False

        for tag in self.run_doc['tags']:
            if name == tag['name']:
                return True
        return False

    def shutdown(self):
        """Runs at end and can be overloaded by subclasses
        """
        pass
