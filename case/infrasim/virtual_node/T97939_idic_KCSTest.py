from case.CBaseCase import *
from lib.Apps import md5
import urllib
import re
import gevent

PROMPT_GUEST = "root@vNode:~$"

class T97939_idic_KCSTest(CBaseCase):
    '''
    [Purpose ]: 
    [Author  ]: @emc.com
    [Sprint  ]: Lykan Sprint 
    [Tickets ]: SST-
    '''
    def __init__(self):
        CBaseCase.__init__(self, self.__class__.__name__)
    
    def config(self):
        CBaseCase.config(self)
        self.enable_node_ssh()

        MD5_KCS_IMG = "cfdf7d855d2f69c67c6e16cc9b53f0da"
        if not os.path.exists("kcs.img"):
            self.log('INFO', "No kcs.img for test, download now...")
            urllib.urlretrieve("https://github.com/InfraSIM/test/raw/master/image/kcs.img", "kcs.img")
        elif md5("kcs.img") != MD5_KCS_IMG:
            self.log('WARNING', "kcs.img fail on md5 sum, delete and download now...")
            os.remove("kcs.img")
            urllib.urlretrieve("https://github.com/InfraSIM/test/raw/master/image/kcs.img", "kcs.img")
        else:
            self.log("INFO", "kcs.img is correct for test")

    def test(self):
        gevent.joinall([gevent.spawn(self.test_ipmi_ssh_on_kcs, obj_node)
                        for obj_node in self.stack.walk_node()])

    def deconfig(self):
        # To do: Case specific deconfig
        CBaseCase.deconfig(self)

    def test_ipmi_ssh_on_kcs(self, node):
        dst_path = node.send_file("kcs.img", "kcs.img")
        str_node_name = node.get_instance_name()
        payload = [
            {
                "controller": {
                    "type": "ahci",
                    "max_drive_per_controller": 6,
                    "drives": [{"size": 8, "file": dst_path}]
                }
            }
        ]
        node.update_instance_config(str_node_name, payload, "compute", "storage_backend")

        # Set boot order to hard disk
        self.log('INFO', 'Set next boot option to disk on {}...'.format(node.get_name()))
        ret, rsp = node.get_bmc().ipmi.ipmitool_standard_cmd("chassis bootdev disk")
        if ret != 0:
            self.result(BLOCK, "Fail to set instance {} on {} boot from disk".
                        format(str_node_name, node.get_ip()))

        # Reboot to kcs.img
        self.log('INFO', 'Power cycle guest to boot to disk on {}...'.format(node.get_name()))
        ret, rsp = node.get_bmc().ipmi.ipmitool_standard_cmd("chassis power cycle")
        if ret != 0:
            self.result(BLOCK, "Fail to set instance {} on {} boot from disk".
                        format(str_node_name, node.get_ip()))
            return

        qemu_config = node.get_instance_config(str_node_name)
        qemu_first_mac = qemu_config["compute"]["networks"][0]["mac"].lower()
        # Get qemu IP
        rsp = node.ssh.send_command_wait_string(str_command=r"arp -e | grep {} | awk '{{print $1}}'".
                                                format(qemu_first_mac)+chr(13),
                                                wait="~$")
        qemu_first_ip = rsp.splitlines()[1]
        if not is_valid_ip(qemu_first_ip):
            self.result(BLOCK, "Fail to get virtual compute IP address on {} {}".
                        format(node.get_name(), node.get_ip()))
            return
        # SSH to guest
        node.ssh.send_command_wait_string(str_command="ssh root@{}".format(qemu_first_ip)+chr(13),
                                          wait=["(yes/no)", "password"])
        match_index = node.ssh.get_match_index()
        if match_index == 0:
            self.result(BLOCK, "Fail to ssh to guest on {} {}".
                        format(node.get_name(), node.get_ip()))
            return
        elif match_index == 1:
            node.ssh.send_command_wait_string(str_command="yes"+chr(13),
                                              wait="password")
        node.ssh.send_command_wait_string(str_command="root"+chr(13),
                                          wait=PROMPT_GUEST)

        self.kcs_test_fru_print(node)
        self.kcs_test_lan_print(node)
        self.kcs_test_sensor_list(node)
        self.kcs_test_sel_list(node)

    def kcs_test_fru_print(self, node):
        rsp = node.ssh.send_command_wait_string(str_command='ipmitool fru print'+chr(13),
                                                wait=PROMPT_GUEST)
        if 'Product Name' not in rsp:
            self.result(FAIL, 'Node {} host get "frp print" result on KCS is unexpected, rsp\n{}'.
                        format(node.get_name(), json.dumps(rsp, indent=4)))
        self.log('INFO', 'rsp: \n{}'.format(rsp))

        rsp = node.ssh.send_command_wait_string(str_command='ipmitool fru print 0'+chr(13),
                                                wait=PROMPT_GUEST)
        if 'Product Name' not in rsp:
            self.result(FAIL, 'Node {} host get "frp print 0" result on KCS is unexpected\n{}'.
                        format(node.get_name(), json.dumps(rsp, indent=4)))
        self.log('INFO', 'rsp: \n{}'.format(rsp))

    def kcs_test_lan_print(self, node):
        ret, rsp = node.get_bmc().ipmi.ipmitool_standard_cmd('fru print 0')

        fru = {}
        for item in rsp.split('\n'):
            key = item.split(': ')[0].strip()
            value = item.split(': ')[-1].strip()
            fru[key] = value

        try:
            node_lan_channel = self.data[fru['Product Name']]

        except KeyError, e:
            self.result(BLOCK,
                        """
                        KeyError: {}.
                        Please supplement product name of node ({}) and the
                        corresponding lan channel in {}.json.
                        """
                        .format(e, e, self.__class__.__name__))

        rsp = node.ssh.send_command_wait_string(str_command='ipmitool lan print {}'.
                                                format(node_lan_channel)+chr(13),
                                                wait=PROMPT_GUEST)
        self.log('INFO', 'rsp: \n{}'.format(rsp))

        if "IP Address" not in rsp:
            self.result(FAIL, 'IPMI command via kcs on node {} fail: lan print {}'.
                        format(node.get_name(), node_lan_channel))

    def kcs_test_sensor_list(self, node):

        rsp = node.ssh.send_command_wait_string(str_command='ipmitool sensor list'+chr(13),
                                                wait=PROMPT_GUEST)
        self.log('INFO', 'rsp: \n{}'.format(rsp))

        # To match "degree" and "discrete".
        # Any system should show both word in sensor list info if sensor list display normally.
        is_match_discrete = re.search(r'discrete', format(rsp))
        is_match_degress = re.search(r'degree', format(rsp))
        if is_match_discrete is None or is_match_degress is None:
            self.result(FAIL, 'IPMI command via kcs on node {} fail: ipmitool sensor list'.
                        format(node.get_name()))

    def kcs_test_sel_list(self, node):

        rsp = node.ssh.send_command_wait_string(str_command='ipmitool sel clear'+chr(13),
                                                wait=PROMPT_GUEST)

        rsp = node.ssh.send_command_wait_string(str_command='ipmitool sel list'+chr(13),
                                                wait=PROMPT_GUEST)
        self.log('INFO', 'rsp: \n{}'.format(rsp))

        # To match "Log area reset/cleared".
        if "Log area reset/cleared" not in rsp:
            self.result(FAIL, 'IPMI command via kcs on node {} fail: ipmitool sel list'.
                        format(node.get_name()))
