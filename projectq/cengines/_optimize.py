#   Copyright 2017 ProjectQ-Framework (www.projectq.ch)
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

"""
Contains a local optimizer engine.
"""

from copy import deepcopy as _deepcopy
from projectq.cengines import LastEngineException, BasicEngine
from projectq.ops import FlushGate, FastForwardingGate, NotMergeable


class LocalOptimizer(BasicEngine):
    """
    LocalOptimizer is a compiler engine which optimizes locally (e.g. merging
    rotations, cancelling gates with their inverse) in a local window of user-
    defined size.
    It stores all commands in a dict of lists, where each qubit has its own
    gate pipeline. After adding a gate, it tries to merge / cancel successive
    gates using the get_merged and get_inverse functions of the gate (if
    available). For examples, see BasicRotationGate. Once a list corresponding
    to a qubit contains >=m gates, the pipeline is sent on to the next engine.
    """
    def __init__(self, m=5):
        """
        Initialize a LocalOptimizer object.
        Args:
            m (int): Number of gates to cache per qubit, before sending on the
                first gate.
        """
        BasicEngine.__init__(self)
        self._l = dict()  # dict of lists containing operations for each qubit
        self._m = m  # wait for m gates before sending on

    def _send_qubit_pipeline(self, idx, n):
        """
        Send n gate operations of the qubit with index idx to the next engine.
        """
        cmd_list = self._l[idx]  # command list for qubit idx
        for i in range(min(n, len(cmd_list))):  # loop over first n commands
            # send all gates before nth gate for other qubits involved
            # --> recursively call send_helper
            other_involved_qubits = [qb
                                     for qreg in cmd_list[i].all_qubits
                                     for qb in qreg
                                     if qb.id != idx]
            for qb in other_involved_qubits:
                Id = qb.id
                try:
                    gateloc = 0
                    # find location of this gate within its list
                    while self._l[Id][gateloc] != cmd_list[i]:
                        gateloc += 1

                    gateloc = self._optimize(Id, gateloc)
                    # flush the gates before the n-qubit gate
                    self._send_qubit_pipeline(Id, gateloc)
                    # delete the nth gate, we're taking care of it
                    # and don't want the other qubit to do so
                    self._l[Id] = self._l[Id][1:]
                except IndexError:
                    print("Invalid qubit pipeline encountered (in the"
                          " process of shutting down?).")

            # all qubits that need to be flushed have been flushed
            # --> send on the n-qubit gate
            self.send([cmd_list[i]])
        # n operations have been sent on --> resize our gate list
        self._l[idx] = self._l[idx][n:]

    def _get_gate_indices(self, idx, i, IDs):
        """
        Return all indices of a command, each index corresponding to the
        command's index in one of the qubits' command lists.
        Args:
            idx (int): qubit index
            i (int): command position in qubit idx's command list
            IDs (list<int>): IDs of all qubits involved in the command
        """
        N = len(IDs)
        # 1-qubit gate: only gate at index i in list #idx is involved
        if N == 1:
            return [i]

        # When the same gate appears multiple time, we need to make sure not to
        # match earlier instances of the gate applied to the same qubits. So we
        # count how many there are, and skip over them when looking in the
        # other lists.
        cmd = self._l[idx][i]
        num_identical_to_skip = sum(1
                                    for prev_cmd in self._l[idx][:i]
                                    if prev_cmd == cmd)
        indices = []
        for Id in IDs:
            identical_indices = [i
                                 for i, c in enumerate(self._l[Id])
                                 if c == cmd]
            indices.append(identical_indices[num_identical_to_skip])
        return indices

    def _delete_command(self, idx, command_idx):
        """ 
        Deletes the command at self._l[idx][command_idx] accounting 
        for all qubits in the optimizer dictionary. 
        """
        # List of the indices of the qubits that are involved
        # in command
        qubitids = [qb.id for sublist in self._l[idx][command_idx].all_qubits
                for qb in sublist]
        # List of the command indices corresponding to the position
        # of this command on each qubit id 
        commandidcs = self._get_gate_indices(idx, command_idx, qubitids)
        for j in range(len(qubitids)):
            try:
                new_list = (self._l[qubitids[j]][0:commandidcs[j]] +
                            self._l[qubitids[j]][commandidcs[j]+1:])
            except: 
                # If there are no more commands after that being deleted.
                new_list = (self._l[qubitids[j]][0:commandidcs[j]])
            self._l[qubitids[j]] = new_list

    def _replace_command(self, idx, command_idx, new_command):
        """ 
        Replaces the command at self._l[idx][command_idx] accounting 
        for all qubits in the optimizer dictionary. 
        """
        # List of the indices of the qubits that are involved
        # in command
        qubitids = [qb.id for sublist in self._l[idx][command_idx].all_qubits
                for qb in sublist]
        # List of the command indices corresponding to the position
        # of this command on each qubit id 
        commandidcs = self._get_gate_indices(idx, command_idx, qubitids)
        for j in range(len(qubitids)):
            try:
                new_list = (self._l[qubitids[j]][0:commandidcs[j]] 
                            + [new_command]
                            + self._l[qubitids[j]][commandidcs[j]+1:])
            except: 
                # If there are no more commands after that being replaced.
                new_list = (self._l[qubitids[j]][0:commandidcs[j]] + [new_command])
            self._l[qubitids[j]] = new_list

    def _get_erase(self, idx, qubitids, commandidcs, inverse_command):
        """
        Determines whether inverse commands should be cancelled
        with one another. i.e. the commands between the pair are all
        commutable for each qubit involved in the command.
        """
        erase = True
        # We dont want to examine qubit idx because the optimizer has already
        # checked that the gates between the current and mergeable gates are
        # commutable (or a commutable list).
        commandidcs.pop(qubitids.index(idx)) # Remove corresponding position of command for qubit idx from commandidcs
        qubitids.remove(idx) # Remove qubitid representing the current qubit in optimizer
        x=1
        for j in range(len(qubitids)):
            # Check that any gates between current gate and inverse
            # gate are all commutable
            this_command = self._l[qubitids[j]][commandidcs[j]]
            future_command = self._l[qubitids[j]][commandidcs[j]+x]
            while (future_command!=inverse_command):
                if (this_command.is_commutable(future_command)):
                    x+=1
                    future_command = self._l[qubitids[j]][commandidcs[j]+x]
                    erase = True
                else:
                    erase = False
                    break
        return erase

    def _get_merge_boolean(self, idx, qubitids, commandidcs, merged_command):
        """
        To determine whether mergeable commands should be merged
        with one another. i.e. the commands between them are all
        commutable, for each qubit involved in the command. It does
        not check for the situation where commands are separated by
        a commutable list. However other parts of the optimizer 
        should find this situation.
        """
        merge = True
        # We dont want to examine qubit idx because the optimizer has already
        # checked that the gates between the current and mergeable gates are
        # commutable (or a commutable list).
        commandidcs.pop(qubitids.index(idx)) # Remove corresponding position of command for qubit idx from commandidcs
        qubitids.remove(idx) # Remove qubitid representing the current qubit in optimizer
        for j in range(len(qubitids)):
            # Check that any gates between current gate and mergeable
            # gate are commutable
            this_command = self._l[qubitids[j]][commandidcs[j]]
            possible_command = None
            merge = True
            x=1
            while (possible_command!=merged_command):
                future_command = self._l[qubitids[j]][commandidcs[j]+x]
                try:
                    possible_command = this_command.get_merged(future_command)
                except:
                    pass
                if (possible_command==merged_command):
                    merge = True
                    break
                if (this_command.is_commutable(future_command)==1): 
                    x+=1
                    merge = True
                    continue
                else:
                    merge = False
                    break
        return merge

    def _optimize(self, idx, lim=None):
        """
        Try to remove identity gates using the is_identity function, 
        then merge or even cancel successive gates using the get_merged and
        get_inverse functions of the gate (see, e.g., BasicRotationGate).
        It does so for all qubit command lists.
        """
        # loop over all qubit indices
        i = 0
        limit = len(self._l[idx])
        if lim is not None:
            limit = lim

        while i < limit - 1:
            command_i = self._l[idx][i]
            command_i_plus_1 = self._l[idx][i+1]

            # Delete command i if it is equivalent to identity
            if command_i.is_identity():
                self._delete_command(idx, i)
                i = 0
                limit -= 1
                continue
            
            x = 0
            i_x_com = True # This boolean should be updated to represent whether
            # the gates following i, up to and including x, are commutable
            while (i+x+1 < limit):
                if i_x_com:
                    # Gate i is commutable with each gate up to i+x, so 
                    # check if i and i+x+1 can be cancelled or merged
                    inv = self._l[idx][i].get_inverse()
                    if inv == self._l[idx][i+x+1]:
                        # List of the indices of the qubits that are involved
                        # in command
                        qubitids = [qb.id for sublist in self._l[idx][i].all_qubits
                            for qb in sublist]
                        # List of the command indices corresponding to the position
                        # of this command on each qubit id 
                        commandidcs = self._get_gate_indices(idx, i, qubitids)
                        erase = True
                        erase = self._get_erase(idx, qubitids, commandidcs, inv)
                        if erase:
                        # Delete the inverse commands. Delete the later
                        # one first so the first index doesn't 
                        # change before you delete it.
                            self._delete_command(idx, i+x+1)
                            self._delete_command(idx, i)
                            i = 0
                            limit -= 2
                            break
                        # Unsuccessful in cancelling inverses, try merging.
                        pass
                    try:
                        merged_command = self._l[idx][i].get_merged(self._l[idx][i+x+1])
                        # determine index of this gate on all qubits
                        qubitids = [qb.id for sublist in self._l[idx][i].all_qubits
                                    for qb in sublist]
                        commandidcs = self._get_gate_indices(idx, i, qubitids)
                        merge = True
                        merge = self._get_merge_boolean(idx, qubitids, commandidcs, 
                                                                merged_command)
                        if merge:
                            # Delete command i+x+1 first because i+x+1
                            # will not affect index of i
                            self._delete_command(idx, i+x+1)
                            self._replace_command(idx, i, merged_command)
                            i = 0
                            limit -= 1
                            break
                    except NotMergeable:
                        # Unsuccessful in merging, see if gates are commutable
                        pass  

                    if(self._l[idx][i].is_commutable(self._l[idx][i+x+1]) == 1):
                        x=x+1
                        continue

                    if(self._l[idx][i].is_commutable(self._l[idx][i+x+1]) == 2):
                        # See if self._l[idx][i+x] is part of a gate list which 
                        # is commutable with self._l[idx][i]
                        # commutable_gate_list = a property of self._l[idx][i].gate
                        commutable_gate_lists = self._l[idx][i].gate.get_commutable_gate_lists()
                        # Keep a list of commutable_lists that start with 
                        # self._l[idx][i+x]
                        commutable_lists = []
                        for gate_list in commutable_gate_lists:
                            if (gate_list[0].__class__ == self._l[idx][i+x+1].gate.__class__):
                                commutable_lists.append(gate_list)
                        # Iterate through the next gates after i+x and delete 
                        # any list in commutable_lists which doesn't contain
                        # the same gates as self._l[idx][i+x] onwards
                        y=0
                        i_x_com=False
                        while(len(commutable_lists)>0):
                            # If no commutable lists, move on to next i
                            for l in commutable_lists:
                                if (y>(len(l)-1)):
                                # Up to the yth term in l, we have checked
                                # that self._l[idx][i+x+y] == l[y]
                                # This means the list l is commutable 
                                # with self._l[idx][i]
                                    # Set x = x+len(l)-1 and continue through while loop
                                    # As though the list was a commutable gate
                                    x+=(len(l))
                                    commutable_lists=[]
                                    i_x_com=True
                                    break
                                if (l[y].__class__==self._l[idx][i+x+1+y].gate.__class__):
                                    y+=1
                                    continue
                                else:
                                    commutable_lists.pop(l)
                                    break
                    # At this point, if the commands following i+x are the same as a
                    # list l which is commutable with i, then we have added len(l) to 
                    # x and set i_x_com to True. If the commands do not make up a list
                    # l then i_x_com = False and we should move on to the next i.          
                        continue
                    break
                else:
                    break
  
            i += 1  # next iteration: look at next gate
        return limit

    def _check_and_send(self):
        """
        Check whether a qubit pipeline must be sent on and, if so,
        optimize the pipeline and then send it on.
        """
        for i in self._l:
            if (len(self._l[i]) >= self._m or len(self._l[i]) > 0 and
                    isinstance(self._l[i][-1].gate, FastForwardingGate)):
                self._optimize(i)
                if (len(self._l[i]) >= self._m and not
                        isinstance(self._l[i][-1].gate,
                                   FastForwardingGate)):
                    self._send_qubit_pipeline(i, len(self._l[i]) - self._m + 1)
                elif (len(self._l[i]) > 0 and
                      isinstance(self._l[i][-1].gate, FastForwardingGate)):
                    self._send_qubit_pipeline(i, len(self._l[i]))
        new_dict = dict()
        for idx in self._l:
            if len(self._l[idx]) > 0:
                new_dict[idx] = self._l[idx]
        self._l = new_dict

    def _cache_cmd(self, cmd):
        """
        Cache a command, i.e., inserts it into the command lists of all qubits
        involved.
        """
        # are there qubit ids that haven't been added to the list?
        idlist = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]

        # add gate command to each of the qubits involved
        for ID in idlist:
            if ID not in self._l:
                self._l[ID] = []
            self._l[ID] += [cmd]

        self._check_and_send()

    def receive(self, command_list):
        """
        Receive commands from the previous engine and cache them.
        If a flush gate arrives, the entire buffer is sent on.
        """
        for cmd in command_list:
            if cmd.gate == FlushGate():  # flush gate --> optimize and flush
                for idx in self._l:
                    self._optimize(idx)
                    self._send_qubit_pipeline(idx, len(self._l[idx]))
                new_dict = dict()
                for idx in self._l:
                    if len(self._l[idx]) > 0:
                        new_dict[idx] = self._l[idx]
                self._l = new_dict
                assert self._l == dict()
                self.send([cmd])
            else:
                self._cache_cmd(cmd)