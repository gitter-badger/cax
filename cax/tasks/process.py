"""Process raw data into processed data

Performs batch queue operations to run pax.
"""

import datetime
import hashlib
import subprocess
import sys
import os
from collections import defaultdict
import time
import pax
import checksumdir
import json
#from pymongo import ReturnDocument

from cax import qsub, config
from cax.task import Task
from cax.api import api

def verify():
    """Verify the file

    Now is nothing.  Could check number of events later?
    """
    return True


def _process(name, in_location, host, pax_version,
             out_location, ncpus=1, disable_updates=False, json_file=""):
    """Called by another command.
    """
    print('Welcome to cax-process')

    # Import pax so can process the data
    from pax import core, parallel, configuration   

    # Grab the Run DB if not passing json so we can query it
    if json_file == "":
        collection = config.mongo_collection()
        # This query is used to find if this run has already processed this data
        # in the same way.  If so, quit.
        query = {'name'    : name,
                'detector' : detector,
                 # This 'data' gets deleted later and only used for checking
                'data'    : {'$elemMatch': {'host'       : host,
                                            'type'       : 'processed',
                                            'pax_version': pax_version}}}
        doc = collection.find_one(query)  # Query DB
        
        if doc is not None:
            print("Already processed %s.  Clear first.  %s" % (name,
                                                            pax_version))
            return 1

        # New data location
        datum = {'host': host,
                 'type': 'processed',
                 'pax_hash': pax_hash,
                 'pax_version': pax_version,
                 'status': 'transferring',
                 'location': output_fullname + '.root',
                 'checksum': None,
                 'creation_time': datetime.datetime.utcnow(),
                 'creation_place': host}

        # Not processed this way already, so notify run DB we will
        doc = collection.find_one_and_update({'detector': detector, 'name': name},
                                            {'$push': {'data': datum}},
                                            return_document=ReturnDocument.AFTER)

    # if processing on OSG (given json file)
    else:
        with open(json_file, "r") as doc_file:
            doc = json.load(doc_file)

    detector = doc['detector']

    basedir = out_location
    if host == 'login':
        basedir = 'output'
    
    if detector == 'muon_veto':
        output_fullname = basedir + '/' + name + '_MV'
    elif detector == 'tpc':
        output_fullname = basedir + '/' + name

    os.makedirs(basedir, exist_ok=True)

    # New data location
    datum = {'host'          : host,
             'type'          : 'processed',
             'pax_version'   : pax_version,
             'status'        : 'transferring',
             'location'      : output_fullname + '.root',
             'checksum'      : None,
             'creation_time' : datetime.datetime.utcnow(),
             'creation_place': host}
    
    # Determine based on run DB what settings to use for processing.
    if detector == 'muon_veto':
        pax_config = 'XENON1T_MV'

    elif detector == 'tpc':

        if doc['reader']['self_trigger']:
            pax_config = 'XENON1T'
        else:
            pax_config = 'XENON1T_LED'

    pax_db_call = True
    if json_file != "":
        pax_db_call = False
        
    config_dict = {'pax': {'input_name' : in_location,
                           'output_name': output_fullname,
                           'n_cpus'     : ncpus,
                           'look_for_config_in_runs_db' : pax_db_call
                           }}

    mongo_config = doc['processor']
    config_dict = configuration.combine_configs(mongo_config,config_dict)

    # Add run number and run name to the config_dict
    config_dict.setdefault('DEFAULT', {})
    config_dict['DEFAULT']['run_number'] = doc['number']
    config_dict['DEFAULT']['run_name'] = doc['name'] 

    if host == 'midway-login1' and ncpus > 1:
        config_dict['DEFAULT']['lock_breaking_timeout'] = 600

        config_dict.setdefault('Queues', {})
        config_dict['Queues']['event_block_size'] = 1
        config_dict['Queues']['max_blocks_on_heap'] = 1000
        config_dict['Queues']['timeout_after_sec'] = 600
        
    # Try to process data.
    try:
        print('processing', name, in_location, pax_config)
        print('saving to', output_fullname)
        
        pax_kwargs = dict(config_names=pax_config,
                          config_dict=config_dict)
        
        if ncpus > 1:
            parallel.multiprocess_locally(n_cpus=ncpus, **pax_kwargs)
        else:
            core.Processor(**pax_kwargs).run()

    except Exception as exception:
        # Data processing failed.
        if json_file == "":
            datum['status'] = 'error'
            if config.DATABASE_LOG == True:
                collection.update(query, {'$set': {'data.$': datum}})
        raise

    if json_file != "":

        datum['status'] = 'verifying'
        if config.DATABASE_LOG == True:
            collection.update(query, {'$set': {'data.$': datum}})

        datum['checksum'] = checksumdir._filehash(datum['location'],
                                                  hashlib.sha512)
        if verify():
            datum['status'] = 'transferred'
        else:
            datum['status'] = 'error'

        if config.DATABASE_LOG == True:
            collection.update(query, {'$set': {'data.$': datum}})

        
class ProcessBatchQueue(Task):
    "Create and submit job submission script."

    def __init__(self):
        self.API = api()

        thishost = config.get_hostname()
        version = 'v%s' % pax.__version__

        query = {"data" : {"$not" : {"$elemMatch" : {"host" : thishost,
                                                     "type" : "processed",
                                                     "pax_version" : version
                                                     }
                                     },
                           "$elemMatch" : {"host" : thishost,
                                           "type" : "raw"}
                           },
                 'reader.ini.write_mode' : 2,
                 'trigger.events_built' : {"$gt" : 0},
                 'processor.DEFAULT.gains' : {'$exists' : True},
                 'processor.DEFAULT.electron_lifetime_liquid' : {'$exists' : True},
                 'tags' : {"$not" : {'$elemMatch' : {'name' : 'donotprocess'}}},
                 }
        Task.__init__(self, query = query)
    
    def verify(self):
        """Verify processing worked"""
        return True  # yeah... TODO.

    def submit(self, in_location, host, pax_version, out_location,
               ncpus, disable_updates, json_file):
        '''Submission Script
        '''

        name = self.run_doc['name']
        number = self.run_doc['number']

        script_args = dict(host=host,
                           name=name,
                           pax_version='v%s' % pax.__version__,
                           number=number,
                           out_location = out_location,
                           ncpus = ncpus,
                           disable_updates = disable_updates,
                           json_file = json_file
                           )

        script = config.processing_script(script_args)
        self.log.info(script)

        if host == 'login':
            outer_dag_dir = "/xenon/ershockley/cax/{pax_version}/{name}/dags".format(name=name,
                                                                                      pax_version=pax_version)
            inner_dag_dir = outer_dag_dir + "/inner_dags"

            if not os.path.exists(inner_dag_dir):
                os.makedirs(inner_dag_dir)

            joblog_dir = "/xenon/ershockley/cax/{pax_version}/{name}/joblogs".format(name=name, pax_version=pax_version)

            if not os.path.exists(joblog_dir):
                os.mkdir(joblog_dir)
            
            outer_dag_file = outer_dag_dir + "/{name}_outer.dag".format(name=name)
            inner_dag_file = inner_dag_dir + "/{name}_inner.dag".format(name=name)

            qsub.submit_dag_job(number, outer_dag_file, inner_dag_file, out_location, script, pax_version, json_file)

        else:
            qsub.submit_job(host, script, name + "_" + pax_version)
                        
    def each_run(self):

        # check if too many jobs on grid 
        if len(qsub.get_queue()) > 1000:
            self.log.info("Too many jobs in queue, wait 2 minutes and try again")
            time.sleep(120)
            return

        thishost = config.get_hostname()

        disable_updates = not config.DATABASE_LOG

        pax_version = 'v%s' % pax.__version__

        have_processed, have_raw = self.local_data_finder(thishost, pax_version)

        if have_processed or not have_raw:
            self.log.debug("Skipping %s already processed or no raw",
                           self.run_doc['name'])
            return

        # Get number of events in data set (not set for early runs <1000)
        events = self.run_doc.get('trigger', {}).get('events_built', 0)

        # Specify number of cores for pax multiprocess
        ncpus = 1
        # 1 CPU for small number of events (sometimes pax stalls
        # with too many CPU)
        if events > 1000 and thishost == 'midway-login1':
            ncpus = 4  # based on Figure 2 here https://xecluster.lngs.infn.it/dokuwiki/doku.php?id=xenon:xenon1t:shockley:performance#automatic_processing

        out_location = config.get_processing_dir(thishost, pax_version)
        if not os.path.exists(out_location):
            os.makedirs(out_location)

        queue_list = qsub.get_queue(thishost)
        
        # Warning: No check for pax version here
        if self.run_doc['name'] in queue_list:
            self.log.debug("Skipping %s currently in queue",
                           self.run_doc['name'])
            return

        self.log.info("Processing %s with pax_%s, output to %s",
                      self.run_doc['name'], pax_version, out_location)

        if thishost != 'login':
            _process(self.run_doc['name'], have_raw['location'], thishost,
                     pax_version, out_location, ncpus)

        else:
            # New data location
            datum = {'host'          : thishost,
                     'type'          : 'processed',
                     'pax_version'   : pax_version,
                     'status'        : 'transferring',
                     'location'      : out_location + "/" + str(self.run_doc['name']),
                     'checksum'      : None,
                     'creation_time' : datetime.datetime.utcnow(),
                     'creation_place': thishost}

            json_file = "/xenon/ershockley/jsons/" + str(self.run_doc['name']) + ".json"
            with open(json_file, "w") as f:
                json.dump(self.run_doc, f)

            self.submit(have_raw, thishost, pax_version,
                        out_location, ncpus, disable_updates, json_file)

            if config.DATABASE_LOG == True:
                self.API.add_location(doc['_id'], datum)

            time.sleep(5)

    def local_data_finder(self, thishost, version):
        have_processed = False
        have_raw = False
        # Iterate over data locations to know status
        for datum in self.run_doc['data']:

            # Is host known?
            if 'host' not in datum:
                continue

            # If the location doesn't refer to here, skip
            if datum['host'] != thishost:
                continue

            # Raw data must exist
            if datum['type'] == 'raw' and datum['status'] == 'transferred':
                have_raw = datum

            # Check if processed data already exists in DB
            if datum['type'] == 'processed':
                if version == datum['pax_version'] and datum['status'] == 'transferred':
                    have_processed= True

        return have_processed, have_raw


# Arguments from process function: (name, in_location, host, pax_version,
#                                   out_location, ncpus):
def main():
    _process(*sys.argv[1:])
