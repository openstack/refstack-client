#!/usr/bin/env python
#
# Copyright (c) 2014 Piston Cloud Computing, Inc. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#


"""
Run Tempest and upload results to Refstack.

This module runs the Tempest test suite on an OpenStack environment given a
Tempest configuration file.

"""

import argparse
import ConfigParser
import json
import logging
import os
import subprocess
import time

from keystoneclient.v2_0 import client as ksclient

from subunit_processor import SubunitProcessor


class RefstackClient:
    log_format = "%(asctime)s %(name)s:%(lineno)d %(levelname)s %(message)s"

    def __init__(self, args):
        '''Prepare a tempest test against a cloud.'''
        self.logger = logging.getLogger("refstack_client")
        self.console_log_handle = logging.StreamHandler()
        self.console_log_handle.setFormatter(
            logging.Formatter(self.log_format))
        self.logger.addHandler(self.console_log_handle)

        if args.verbose > 1:
            self.logger.setLevel(logging.DEBUG)
        elif args.verbose == 1:
            self.logger.setLevel(logging.INFO)
        else:
            self.logger.setLevel(logging.ERROR)

        # assign local vars to match args
        self.url = args.url
        self.tempest_dir = args.tempest_dir
        self.tempest_script = os.path.join(self.tempest_dir, 'run_tempest.sh')
        self.test_cases = args.test_cases
        self.verbose = args.verbose
        self.offline = args.offline

        # Check that the config file exists.
        if not os.path.isfile(args.conf_file):
            self.logger.error("File not valid: %s" % args.conf_file)
            exit(1)

        self.conf_file = args.conf_file
        self.conf = ConfigParser.SafeConfigParser()
        self.conf.read(self.conf_file)

        self.results_file = self._get_subunit_output_file()
        self.cpid = self._get_cpid_from_keystone()

    def post_results(self, content):
        '''Post the combined results back to the server.'''

        # TODO(cdiep): Post results once the API is available as outlined here:
        # github.com/stackforge/refstack/blob/master/specs/approved/api-v1.md

        self.logger.debug('API request content: %s ' % content)

    def _get_subunit_output_file(self):
        '''This method reads from the next-stream file in the .testrepository
           directory of the given Tempest path. The integer here is the name
           of the file where subunit output will be saved to.'''
        try:
            subunit_file = open(os.path.join(
                                self.tempest_dir, '.testrepository',
                                'next-stream'), 'r').read().rstrip()
        except (IOError, OSError):
            self.logger.debug('The .testrepository/next-stream file was not '
                              'found. Assuming subunit results will be stored '
                              'in file 0.')

            # Testr saves the first test stream to .testrepository/0 when
            # there is a newly generated .testrepository directory.
            subunit_file = "0"

        return os.path.join(self.tempest_dir, '.testrepository', subunit_file)

    def _get_cpid_from_keystone(self):
        '''This will get the Keystone service ID which is used as the CPID.'''
        try:
            args = {'auth_url': self.conf.get('identity', 'uri'),
                    'username': self.conf.get('identity', 'admin_username'),
                    'password': self.conf.get('identity', 'admin_password')}

            if self.conf.has_option('identity', 'admin_tenant_id'):
                args['tenant_id'] = self.conf.get('identity',
                                                  'admin_tenant_id')
            else:
                args['tenant_name'] = self.conf.get('identity',
                                                    'admin_tenant_name')

            client = ksclient.Client(**args)
            services = client.services.list()
            for service in services:
                if service.type == "identity":
                    return service.id

        except ConfigParser.Error as e:
            # Most likely a missing section or option in the config file.
            self.logger.error("Invalid Config File: %s" % e)
            exit(1)

    def _form_json_content(self, cpid, duration, results):
        '''This method will create the JSON content for the request.'''
        content = {}
        content['cpid'] = cpid
        content['duration_seconds'] = duration
        content['results'] = results
        return json.dumps(content)

    def get_passed_tests(self, result_file):
        '''Get just the tests IDs in a subunit file that passed Tempest.'''
        subunit_processor = SubunitProcessor(result_file)
        results = subunit_processor.process_stream()
        return results

    def run(self):
        '''Execute tempest test against the cloud.'''
        try:
            self.logger.info("Starting Tempest test...")
            start_time = time.time()

            # Run the tempest script, specifying the conf file and the
            # flag telling it to not use a virtual environment.
            cmd = (self.tempest_script, '-C', self.conf_file, '-N', '-t')

            # Add the tempest test cases to test as arguments.
            if self.test_cases:
                cmd += ('--', self.test_cases)
            else:
                cmd += ('--', "tempest.api")

            # If there were two verbose flags, show tempest results.
            if self.verbose > 1:
                stderr = None
            else:
                # Suppress tempest results output. Note that testr prints
                # results to stderr.
                stderr = open(os.devnull, 'w')

            # Execute the tempest test script in a subprocess.
            process = subprocess.Popen(cmd, stderr=stderr)
            process.communicate()

            end_time = time.time()
            elapsed = end_time - start_time
            duration = int(elapsed)

            self.logger.info('Tempest test complete.')
            self.logger.info('Subunit results located in: %s' %
                             self.results_file)

            results = self.get_passed_tests(self.results_file)
            self.logger.info("Number of passed tests: %d" % len(results))

            # If the user did not specify the offline argument, then upload
            # the results.
            if not self.offline:
                content = self._form_json_content(self.cpid, duration, results)
                self.post_results(content)

        except subprocess.CalledProcessError as e:
            self.logger.error('%s failed to complete' % (e))


def parse_cli_args(args=None):

    parser = argparse.ArgumentParser(description='Starts a tempest test',
                                     formatter_class=argparse.
                                     ArgumentDefaultsHelpFormatter)

    parser.add_argument('-v', '--verbose',
                        action='count',
                        help='Show verbose output. Note that -vv will show '
                             'Tempest test result output.')

    parser.add_argument('--offline',
                        action='store_true',
                        help='Do not upload test results after running '
                             'Tempest.')

    parser.add_argument('--url',
                        action='store',
                        required=False,
                        default='https://api.refstack.org',
                        type=str,
                        help='Refstack API URL to post results to (e.g. --url '
                             'https://127.0.0.1:8000).')

    parser.add_argument('--tempest-dir',
                        action='store',
                        required=False,
                        dest='tempest_dir',
                        default='test_runner/src/tempest',
                        help='Path of the tempest project directory.')

    parser.add_argument('-c', '--conf-file',
                        action='store',
                        required=True,
                        dest='conf_file',
                        type=str,
                        help='Path of the tempest configuration file to use.')

    parser.add_argument('-t', '--test-cases',
                        action='store',
                        required=False,
                        dest='test_cases',
                        type=str,
                        help='Specify a subset of test cases to run '
                             '(e.g. --test-cases tempest.api.compute).')
    return parser.parse_args(args=args)
