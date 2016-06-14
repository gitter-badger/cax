import datetime
import os
import shutil

import pymongo

from cax import config
from cax.task import Task
from cax.tasks import checksum


class ClearDAQBuffer(checksum.CompareChecksums):
    """Perform a checksum on accessible data."""

    def remove_untriggered(self):
        client = pymongo.MongoClient(self.untriggered_data['location'])
        db = client.untriggered
        try:
            db.authenticate('eb',
                            os.environ.get('MONGO_PASSWORD'))
        except pymongo.errors.ServerSelectionTimeoutError as e:
            self.log.error("Mongo error: %s" % str(e))
            return None

        self.log.debug('Dropping %s' % self.untriggered_data['collection'])
        try:
            db.drop_collection(self.untriggered_data['collection'])
        except pymongo.errors.OperationFailure:
            # This usually means some background operation is still running
            self.log.error("Mongo error: %s" % str(e))
            return None

        self.log.info('Dropped %s' % self.untriggered_data['collection'])

        self.log.debug(self.collection.update({'_id': self.run_doc['_id']},
                                              {'$pull': {
                                                  'data': self.untriggered_data}}))

    def each_run(self):
        if self.check(warn=False) > 2 and self.untriggered_data:
            self.remove_untriggered()
        else:
            self.log.debug("Did not drop: %s" % str(self.untriggered_data))


class RetryStalledTransfer(checksum.CompareChecksums):
    """Alert if stale transfer."""

    # Do not overload this routine.
    each_run = Task.each_run

    def has_untriggered(self):
        for data_doc in self.run_doc['data']:
            if data_doc['type'] == 'untriggered':
                return True
        return False

    def each_location(self, data_doc):
        if 'host' not in data_doc or data_doc['host'] != config.get_hostname():
            return  # Skip places where we can't locally access data

        if 'creation_time' not in data_doc:
            self.log.warning("No creation time for %s" % str(data_doc))
            return

        # How long since last update to file
        difference = self.check_file_age(data_doc['location'])
        timeoutseconds = 6 * 3600 # six hours
        
        if data_doc["status"] == "transferred":
            return  # Transfer went fine

        self.log.debug(difference)

        if difference > timeoutseconds:  # If stale transfer
            self.give_error("Transfer %s from run %d (%s) lasting more than "
                            "%i seconds" % (data_doc['type'],
                                            self.run_doc['number'],
                                            self.run_doc['name'],
                                            timeoutseconds))

        if difference > timeoutseconds or \
                        data_doc["status"] == 'error':  # If stale transfer
            self.give_error("Transfer lasting more than %i seconds "
                            "or errored, retry." % (timeoutseconds))

            self.log.info("Deleting %s" % data_doc['location'])

            if os.path.isdir(data_doc['location']):
                shutil.rmtree(data_doc['location'])
                self.log.error('Deleted, notify run database.')
            elif os.path.isfile(data_doc['location']):
                os.remove(data_doc['location'])
            else:
                self.log.error('did not exist, notify run database.')

            resp = self.collection.update({'_id': self.run_doc['_id']},
                                          {'$pull': {'data': data_doc}})
            self.log.error('Removed from run database.')
            self.log.debug(resp)

        def check_file_age(path):
            # Path can be a file or a directory
            modtime = (datetime.datetime.now()-
                       datetime.datetime.strptime
                       (time.ctime(os.path.getmtime(path)),
                        "%a %b %d %H:%M:%S %Y")).total_seconds()

            for subdir, dirs, files in os.walk(path):
                if subdir == path:
                    continue

                sdirtime = check_file_age(subdir)
                if sdirtime < modtime:
                    modtime = sdirtime                    
            return (int)(modtime)
                                                                

class RetryBadChecksumTransfer(checksum.CompareChecksums):
    """Alert if stale transfer."""

    # Do not overload this routine.
    each_run = Task.each_run

    def each_location(self, data_doc):
        if 'host' not in data_doc or data_doc['host'] != config.get_hostname():
            return  # Skip places where we can't locally access data

        if data_doc["status"] != "transferred":
            return

        if data_doc['checksum'] != self.get_main_checksum(**data_doc):
            self.give_error("Bad checksum")
            if self.check(warn=False) > 1:
                self.log.info("Deleting %s" % data_doc['location'])

                if os.path.isdir(data_doc['location']):
                    shutil.rmtree(data_doc['location'])
                    self.log.error('Deleted, notify run database.')
                elif os.path.isfile(data_doc['location']):
                    os.remove(data_doc['location'])
                else:
                    self.log.error('did not exist, notify run database.')

                resp = self.collection.update({'_id': self.run_doc['_id']},
                                              {'$pull': {'data': data_doc}})
                self.log.error('Removed from run database.')
                self.log.debug(resp)
