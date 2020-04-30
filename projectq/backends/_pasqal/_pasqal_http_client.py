#   Copyright 2020 ProjectQ-Framework (www.projectq.ch)
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

""" Back-end to run quantum program on pasqal cloud platform"""

import getpass
import signal
import time

import requests
from requests.compat import urljoin
from requests import Session

# An pasqal token can be requested at:
# https://gateway-portal.pasqal.eu/

_API_URL = 'https://gateway.pasqal.eu/marmot/'


class Pasqal(Session):
    def __init__(self):
        super(Pasqal, self).__init__()
        self.backends = dict()
        self.timeout = 5.0
        self.token = None

    def update_devices_list(self, verbose=False):
        """
        Returns:
            (list): list of available devices

        Up to my knowledge there is no proper API call for online devices,
        so we just assume that the list from pasqal portal always up to date
        """
        # TODO: update once the API for getting online devices is available
        self.backends = dict()
        # Note: The qubit number seems arbitrary at the moment. 36 qubits corresponds to a 6*6 grid.
        self.backends['pasqal_simulator'] = {
            'nq': 9,
            'version': '0.0.1',
            'url': 'sim/'
        }
        if verbose:
            print('- List of pasqal devices available:')
            print(self.backends)

    def is_online(self, device):
        # useless at the moment, may change if API evolves
        return device in self.backends

    def can_run_experiment(self, info, device):
        """
        check if the device is big enough to run the code

        Args:
            info (dict): dictionary sent by the backend containing the code to
                run
            device (str): name of the pasqal device to use
        Returns:
            (bool): True if device is big enough, False otherwise
        """
        nb_qubit_max = self.backends[device]['nq']
        nb_qubit_needed = info['nq']
        return nb_qubit_needed <= nb_qubit_max, nb_qubit_max, nb_qubit_needed

    def _authenticate(self, token=None):
        """
        Args:
            token (str): pasqal user API token.
        """
        if token is None:
            token = getpass.getpass(prompt='pasqal token > ')
        self.headers.update({"Authorization": token})
        self.token = token

    def _run(self, info, device):
        argument = {
            'data': info['circuit'],
            'access_token': self.token,
            'repetitions': info['shots'],
            'no_qubits': info['nq']
        }
        print(urljoin(_API_URL,'/simulate/no-noise/submit'))
        self.headers.update({"Repetitions":  info['shots']})
        #req = super(Pasqal, self).post(urljoin(_API_URL,
        #                                   '/simulate/no-noise/submit'),
        #                           data=argument,
        #                           verify=false)
                                   
        #req.raise_for_status()
        execution_id = '0'#req.text
        if execution_id is None:
            raise Exception('Error in sending the code online')
        return execution_id

    def _get_result(self,
                    device,
                    execution_id,
                    num_retries=3000,
                    interval=1,
                    verbose=False):

        if verbose:
            print("Waiting for results. [Job ID: {}]".format(execution_id))

        original_sigint_handler = signal.getsignal(signal.SIGINT)

        def _handle_sigint_during_get_result(*_):  # pragma: no cover
            raise Exception(
                "Interrupted. The ID of your submitted job is {}.".format(
                    execution_id))

        try:
            signal.signal(signal.SIGINT, _handle_sigint_during_get_result)

            for retries in range(num_retries):
                print(urljoin(_API_URL,'/get-result/'+execution_id))
                #req = super(Pasqal,
                #            self).get(urljoin(_API_URL,
                #                              '/get-result/'+execution_id),
                #                      data=argument,
                #                      verify=False)
                #req.raise_for_status()
                req=[0,1,0,0,0,0]
                if req is not None:
                    print(req)
                    return req#req.json()
                time.sleep(interval)
                if self.is_online(device) and retries % 60 == 0:
                    self.update_devices_list()
                    
                    # TODO: update once the API for getting online devices is
                    #       available
                    if not self.is_online(device):  # pragma: no cover
                        raise DeviceOfflineError(
                            "Device went offline. The ID of "
                            "your submitted job is {}.".format(execution_id))

        finally:
            if original_sigint_handler is not None:
                signal.signal(signal.SIGINT, original_sigint_handler)

        raise Exception("Timeout. The ID of your submitted job is {}.".format(
            execution_id))


class DeviceTooSmall(Exception):
    pass


class DeviceOfflineError(Exception):
    pass


def show_devices(verbose=False):
    """
    Access the list of available devices and their properties (ex: for setup
    configuration)

    Args:
        verbose (bool): If True, additional information is printed

    Returns:
        (list) list of available devices and their properties
    """
    pasqal_session = pasqal()
    pasqal_session.update_devices_list(verbose=verbose)
    return pasqal_session.backends


def retrieve(device,
             token,
             jobid,
             num_retries=3000,
             interval=1,
             verbose=False):
    """
    Retrieves a previously run job by its ID.

    Args:
        device (str): Device on which the code was run / is running.
        token (str): pasqal user API token.
        jobid (str): Id of the job to retrieve

    Returns:
        (list) samples form the pasqal server
    """
    pasqal_session = Pasqal()
    pasqal_session._authenticate(token)
    pasqal_session.update_devices_list(verbose)
    res = pasqal_session._get_result(device,
                                  jobid,
                                  num_retries=num_retries,
                                  interval=interval,
                                  verbose=verbose)
    return res


def send(info,
         device='pasqal_simulator',
         token=None,
         shots=100,
         num_retries=100,
         interval=1,
         verbose=False):
    """
    Sends cicruit through the pasqal API and runs the quantum circuit.

    Args:
        info(dict): Contains representation of the circuit to run.
        device (str): name of the pasqal device. Simulator chosen by default
        token (str): pasqal user API token.
        shots (int): Number of runs of the same circuit to collect
            statistics. max for pasqal is 200.
        verbose (bool): If True, additional information is printed, such as
            measurement statistics. Otherwise, the backend simply registers
            one measurement result (same behavior as the projectq Simulator).

    Returns:
        (list) samples form the pasqal server

    """
    try:
        pasqal_session = Pasqal()

        if verbose:
            print("- Authenticating...")
        if token is not None:
            print('user API token: ' + token)
        pasqal_session._authenticate(token)

        # check if the device is online
        pasqal_session.update_devices_list(verbose)
        online = pasqal_session.is_online(device)
        # useless for the moment
        if not online:  # pragma: no cover
            print("The device is offline (for maintenance?). Use the "
                  "simulator instead or try again later.")
            raise DeviceOfflineError("Device is offline.")

        # check if the device has enough qubit to run the code
        runnable, qmax, qneeded = pasqal_session.can_run_experiment(info, device)
        if not runnable:
            print(
                "The device is too small ({} qubits available) for the code "
                "requested({} qubits needed). Try to look for another device "
                "with more qubits".format(
                    qmax, qneeded))
            raise DeviceTooSmall("Device is too small.")
        if verbose:
            print("- Running code: {}".format(info))
        execution_id = pasqal_session._run(info, device)
        if verbose:
            print("- Waiting for results...")
        res = pasqal_session._get_result(device,
                                      execution_id,
                                      num_retries=num_retries,
                                      interval=interval,
                                      verbose=verbose)
        if verbose:
            print("- Done.")
        return res
    except requests.exceptions.HTTPError as err:
        print("- There was an error running your code:")
        print(err)
    except requests.exceptions.RequestException as err:
        print("- Looks like something is wrong with server:")
        print(err)
    except KeyError as err:
        print("- Failed to parse response:")
        print(err)
