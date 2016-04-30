import datetime
import os
import logging

from paramiko import SSHClient, util
import scp

from cax import config
from cax.task import Task

import subprocess

class CopyBase(Task):

    def copy(self, datum_original, datum_destination, method, option_type):

         if option_type == 'upload':
            config_destination = config.get_config(datum_destination['host'])
            server = config_destination['hostname']
            username = config_destination['username']

        else:
            config_original = config.get_config(datum_original['host'])
            server = config_original['hostname']
            username = config_original['username']

        # Determine method for remote site
        if method == 'scp':
            self.copySCP(datum_original, datum_destination, server, username, option_type)

        elif method == 'gfal-copy':
            self.copyGFAL(datum_original, datum_destination, server, option_type)

        else:
            print (method+" not implemented")
            raise NotImplementedError()

    """Copy data via GFAL function
    """
    def copyGFAL(self, datum_original, datum_destination, server, option_type):

        if upload:
            logging.info("put: %s to %s" % (datum_original['location'],
                                            server+datum_destination['location']))

            status = subprocess.call("gfal-ls -l "+server+datum_destination['location'], shell=True)

            print ("gfal status = ", status)

 
        else:
            logging.info("get: %s to %s" % (server+datum_original['location'],
                                            datum_destination['location']))


    """Copy data via SCP function
    """
    def copySCP(self, datum_original, datum_destination, server, username, option_type)):

        util.log_to_file('ssh.log')
        ssh = SSHClient()
        ssh.load_system_host_keys()

        logging.info("connection to %s" % server)
        #ssh.connect(server,
        #            username=username)

        # SCPCLient takes a paramiko transport as its only argument
        #client = scp.SCPClient(ssh.get_transport())

        logging.info(option_type+": %s to %s" % (datum_original['location'],
                                                 datum_destination['location']))
        
        if option_type == 'upload':
            #client.put(datum_original['location'],
            #           datum_destination['location'],
            #           recursive=True)
        else:
            #client.get(datum_original['location'],
            #           datum_destination['location'],
            #           recursive=True)

        #client.close()


    def do_possible_transfers(self, option_type = 'upload'):
        """Determine candidate transfers
        """

        # Get the 'upload' or 'download' sites.
        options = config.get_options(option_type)

        # If no options, can't do anything
        if options is None:
            return None, None

        # For this run, where do we have transfer access?
        for remote_host in options:
            self.log.debug(remote_host)

            # Get transfer protocol
            method = config.get_config(remote_host)['method'] 
            if not method:
                print ("Must specify transfer protocol (method) for "+remote_host)
                raise

            there = False  # Is data remote?

            datum_here = None  # Information about data here
            datum_there = None # Information about data there

            # Iterate over data locations in DB to know status
            for datum in self.run_doc['data']:

                # Is host known?
                if 'host' not in datum:
                    continue

                transferred =  (datum['status'] == 'transferred')

                # If the location refers to here
                if datum['host'] == config.get_hostname():

                    # If uploading, we should have data
                    if option_type == 'upload' and not transferred:
                        continue

                    datum_here = datum.copy()

                # If the location is a remote host
                elif datum['host'] == remote_host: 

                    # If downloading, they should have data
                    if option_type == 'download' and not transferred:
                        continue

                    datum_there = datum.copy()

            # Upload logic
            if option_type == 'upload' and datum_here and datum_there is None:
                self.copy_handshake(datum_here, remote_host, method, option_type)

            # Download logic
            if option_type == 'download' and datum_there and datum_here is None:
                self.copy_handshake(datum_there, config.get_hostname(), method, option_type)


    def copy_handshake(self, datum, destination, method, option_type):

        destination_config = config.get_config(destination)

        self.log.info(option_type+"ing run %d to: %s" % (self.run_doc['number'],
                                                      destination))

        self.log.debug("Notifying run database")

        datum_new = {'type'    : datum['type'],
                     'host'    : destination,
                     'status'  : 'transferring',
                     'location': os.path.join(destination_config['directory'],
                                              self.run_doc['name']),
                     'checksum': None,
                     'creation_time' : datetime.datetime.utcnow()}

        #self.collection.update({'_id': self.run_doc['_id']},
        #                       {'$push': {'data': datum_new}})

        self.log.info('Starting '+method)

        try:
            self.copy(datum, datum_new, method, option_type)
            datum_new['status'] = 'verifying'

        # WARNING: This needs to be extended to catch gfal-copy errors
        except scp.SCPException as e:
            self.log.exception(e)
            datum_new['status'] = 'error'

        self.log.debug(method+" done, telling run database")

        #self.collection.update({'_id'      : self.run_doc['_id'],
        #                        'data.host': datum_new['host']},
        #                       {'$set': {'data.$': datum_new}})

        self.log.info(option_type+" complete")



class CopyPush(CopyBase):
    """Copy data via SCP to there

    If the data is transfered to current host and does not exist at any other
    site (including transferring), then copy data there."""

    def each_run(self):
        self.do_possible_transfers(option_type = 'upload')


class CopyPull(CopyBase):
    """Copy data via SCP to here

    If data exists at a reachable host but not here, pull it.
    """

    def each_run(self):
        self.do_possible_transfers(option_type = 'download')


