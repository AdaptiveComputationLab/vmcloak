# Copyright (C) 2014-2017 Jurriaan Bremer.
# Copyright (C) 2018 Hatching B.V.
# This file is part of VMCloak - http://www.vmcloak.org/.
# See the file 'docs/LICENSE.txt' for copying permission.

import logging
import os
import subprocess
import time
import libvirt
import  xml.etree.ElementTree as ET

from vmcloak.abstract import Machinery
from vmcloak.data.config import VBOX_CONFIG
from vmcloak.exceptions import CommandError
from vmcloak.paths import get_path
from vmcloak.rand import random_mac
from vmcloak.repository import vms_path
from vmcloak.constants import VMCLOAK_ROOT
from vmcloak.vmxml import Element
log = logging.getLogger(__name__)

class VirtualBox(Machinery):
    FIELDS = VBOX_CONFIG

    def __init__(self, *args, **kwargs):
        Machinery.__init__(self, *args, **kwargs)
        self.vboxmanage = get_path("vboxmanage")

    def _call(self, *args, **kwargs):
        cmd = [self.vboxmanage] + list(args)

        for k, v in kwargs.items():
            if v is None or v is True:
                cmd += ["--" + k]
            else:
                cmd += ["--" + k.rstrip("_"), str(v)]

        try:
            log.debug("Running command: %s", cmd)
            ret = subprocess.check_output(cmd)
        except Exception as e:
            log.error("[-] Error running command: %s", e)
            raise CommandError

        return ret.strip()

    def vminfo(self, element=None):
        ret = {}
        lines = self._call("showvminfo", self.name, machinereadable=True)
        for line in lines.split("\n"):
            key, value = line.split("=", 1)

            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.isdigit():
                value = int(value)

            if key.startswith('"') and key.endswith('"'):
                key = key[1:-1]

            ret[key] = value
        return ret if element is None else ret.get(element)

    def wait_for_state(self, shutdown=False):
        while True:
            try:
                status = self.vminfo("VMState")
                if shutdown and status == "poweroff":
                    break
            except CommandError:
                pass

            time.sleep(1)

    def create_vm(self):
        return self._call("createvm", name=self.name,
                          basefolder=vms_path, register=True)

    def delete_vm(self):
        self._call("unregistervm", self.name, delete=True)

    def ramsize(self, ramsize):
        return self._call("modifyvm", self.name, memory=ramsize)

    def vramsize(self, vramsize):
        return self._call("modifyvm", self.name, vram=vramsize)

    def os_type(self, osversion):
        operating_systems = {
            "winxp": "WindowsXP",
            "win7x86": "Windows7",
            "win7x64": "Windows7_64",
            "win81x86": "Windows81",
            "win81x64": "Windows81_64",
            "win10x86": "Windows10",
            "win10x64": "Windows10_64",
        }
        return self._call("modifyvm", self.name,
                          ostype=operating_systems[osversion])

    def create_hd(self, hdd_path, fsize=256*1024):
        self._call("createhd", filename=hdd_path, size=fsize)
        self._call("storagectl", self.name, name="IDE", add="ide")
        self._call("storageattach", self.name, storagectl="IDE",
                   type_="hdd", device=0, port=0, medium=hdd_path)

    def attach_hd(self, hdd_path, multi=False):
        # When a harddisk is not attached to a Virtual Machine it will quickly
        # be forgotten. This seems to be within a couple of seconds. When this
        # happens, its "type" (multiattach in our case) is also forgotten,
        # resulting in issues when cloning. Therefore we quickly set its state
        # before attaching it to a Virtual Machine, hoping this approach
        # is "good enough".
        self._call("storagectl", self.name, name="IDE", add="ide")
        if multi:
            self._call("modifyhd", hdd_path, type_="multiattach")
        else:
            self._call("modifyhd", hdd_path, type_="normal")
        self._call("storageattach", self.name, storagectl="IDE",
                   type_="hdd", device=0, port=0, medium=hdd_path)

    def compact_hd(self, hdd_path):
        # We first make the HDD "more" compact - this should be basically
        # defragmenting it.
        self._call("modifyhd", hdd_path, compact=True)

    def clone_hd(self, hdd_inpath, hdd_outpath):
        self._call("clonehd", hdd_inpath, hdd_outpath)

    def remove_hd(self):
        time.sleep(1)
        self._call("storagectl", self.name, portcount=0,
                   name="IDE", remove=True)

    def cpus(self, count):
        self._call("modifyvm", self.name, cpus=count, ioapic="on")

    def attach_iso(self, iso_path):
        """Mount an ISO to the Virtual Machine."""
        self._call("storageattach", self.name, storagectl="IDE",
                   type_="dvddrive", port=1, device=0, medium=iso_path)

    def detach_iso(self):
        time.sleep(1)
        self._call("storageattach", self.name, storagectl="IDE",
                   type_="dvddrive", port=1, device=0, medium="emptydrive")

    def set_field(self, key, value):
        return self._call("setextradata", self.name, key, value)

    def modify_mac(self, macaddr=None, index=1):
        if macaddr is None:
            macaddr = random_mac()

        # VBoxManage prefers MAC addresses without colons.
        vbox_mac = macaddr.replace(":", "")

        mac = {"macaddress%d" % index: vbox_mac}
        self._call("modifyvm", self.name, **mac)
        return macaddr

    def hostonly(self, nictype, macaddr=None, adapter=None):
        index = self.network_index() + 1
        if not adapter:
            if os.name == "posix":
                adapter = "vboxnet0"
            else:
                adapter = "VirtualBox Host-Only Ethernet Adapter"

        # Ensure our hostonly interface is actually up and running.
        if adapter not in self._call("list", "hostonlyifs"):
            log.error("Have you configured %s?", adapter)
            log.info("Please refer to the documentation to configure it.")
            return False

        nic = {
            "nic%d" % index: "hostonly",
            "nictype%d" % index: nictype,
            "nicpromisc%d" % index: "allow-all",
            "hostonlyadapter%d" % index: adapter,
            "cableconnected%d" % index: "on",
        }
        self._call("modifyvm", self.name, **nic)
        return self.modify_mac(macaddr, index)

    def nat(self, nictype, macaddr=None):
        index = self.network_index() + 1

        nic = {
            "nic%d" % index: "nat",
            "nictype%d" % index: nictype,
            "nicpromisc%d" % index: "allow-all",
        }
        self._call("modifyvm", self.name, **nic)
        return self.modify_mac(macaddr, index)

    def hwvirt(self, enable=True):
        """Enable or disable the usage of Hardware Virtualization."""
        self._call("modifyvm", self.name, hwvirtex="on" if enable else "off")

    def start_vm(self, visible=False):
        return self._call("startvm", self.name,
                          type_="gui" if visible else "headless")

    def snapshot(self, label, description=""):
        return self._call("snapshot", self.name, "take", label,
                          description=description, live=True)

    def restore_snapshot(self, label=None):
        if label:
            return self._call("snapshot", self.name, "restore", label)
        else:
            return self._call("snapshot", self.name, "restorecurrent")

    def delete_snapshot(self, label):
        return self._call("snapshot", self.name, "delete", label)

    def stopvm(self):
        return self._call("controlvm", self.name, "poweroff")

    def list_settings(self):
        return self._call("getextradata", self.name, "enumerate")

    def mouse(self, type):
        return self._call("modifyvm", self.name, mouse=type)

    def vrde(self, port=3389, password=""):
        return self._call("modifyvm", self.name, vrde="on", vrdeport=port,
                          vrdeproperty="VNCPassword=%s" % password)

    def paravirtprovider(self, provider):
        return self._call("modifyvm", self.name, paravirtprovider=provider)

    def export(self, filepath):
        return self._call(
            "export", self.name, "--output", filepath, "--vsys", "0",
            product="VMCloak",
            producturl="http://vmcloak.org/",
            vendor="Cuckoo Sandbox",
            vendorurl="http://cuckoosandbox.org/",
            description="Cuckoo Sandbox Virtual Machine created by VMCloak",
        )

class KVM(Machinery):
    """Virtualization layer for KVM using libvirt utility."""
    FIELDS = {}

    def __init__(self, domain_path, *args, **kwargs):
        Machinery.__init__(self, *args, **kwargs)
        self.virsh = get_path("virsh")
        self.qemu_img = get_path("qemu-img")
        self.domain_path = domain_path
        self.domain = ET.parse(os.path.join(VMCLOAK_ROOT, 'data/template/qemu.xml'))

        if os.getenv("LIBVIRT_DEFAULT_URI"):
            QEMU_URI = os.getenv("LIBVIRT_DEFAULT_URI")
        else:
            QEMU_URI = "qemu:///system"

        self.virt_conn = libvirt.open(QEMU_URI)

        if self.virt_conn == None:
            log.error('Failed to open connection to qemu:///system')
            exit(1)

    def _call(self, *args, **kwargs):
        cmd = list(args)

        for k, v in kwargs.items():
            if v is None or v is True:
                cmd += ["--" + k]
            else:
                cmd += ["--" + k.rstrip("_"), str(v)]

        try:
            log.debug("Running command: %s", cmd)
            ret = subprocess.check_output(cmd)
        except Exception as e:
            log.error("[-] Error running command ({0}): {1}".format(" ".join(e.cmd), e.output.strip()))
            raise CommandError

        return ret.strip()


    def vminfo(self, element=None):
        """Returns a dictionary with all available information for the
        Virtual Machine."""
        raise

    def create_vm(self):
        """Create a new Virtual Machine."""
        qemu_temp = os.path.join(VMCLOAK_ROOT, 'data/template/qemu.xml')
        xmlconfig = open(qemu_temp).read()
        self.dom = self.virt_conn.defineXML(xmlconfig)
        if self.dom == None:
            log.error('Failed to define a domain from an XML definition.')
            exit(1)

    def delete_vm(self):
        """Delete an existing Virtual Machine and its associated files."""
        return self.dom.undefine()

    def ramsize(self, ramsize, unit='MiB'):
        """Modify the amount of RAM available for this Virtual Machine."""
        if self.domain.xpath('//memory'):
            memory = self.domain.xpath('//memory')[0]
            memory.text = ramsize
            memory.attrib['unit'] = unit

    def vramsize(self, vramsize, vtype='vga'):
        """Modify the amount of Video memory available for this Virtual
        Machine.
        <devices>
        <video>
            <model type='vga' vram='16384' heads='1'>
                <acceleration accel3d='yes' accel2d='yes'/>
            </model>
        </video>
        </devices>
        """
        devices = self.domain.xpath('//devices')
        video = self.domain.xpath('//devices/video')
        if not video:
            video = Element('video')
            modelAttrs = {
                'type': vtype,
                'vram': vramsize,
                'heads': '1'
            }
            model = Element('model', **modelAttrs)
            video.appendChild(model)
            acceleration = Element('acceleration', accel2d="yes", accel3d="yes")
            model.appendChild(acceleration)
            devices.insert(0, video)
        else:
            try:
                model = devices.iter('model').next()
                model.attrib['vram'] = vramsize
            except StopIteration:
                log.debug('corrupted video element found!')
                exit(1)


    def os_type(self, osversion):
        """Set the OS type."""
        pass


    def create_hd(self, disk_path, fmt='qcow2', size='10G'):
        """Create a harddisk."""
        self._call(self.qemu_img, 'create', '-f', fmt, disk_path, size)

    def immutable_hd(self, adapter_type, mode="persistent"):
        """Make a harddisk immutable or normal."""
        raise

    def remove_hd(self, hdd_path):
        """Remove a harddisk."""
        raise

    def clone_hd(self, hdd_outpath):
        """Clone a harddisk."""
        raise

    def cpus(self, count):
        """Set the number of CPUs to assign to this Virtual Machine."""
        if self.domain.xpath('//vcpu'):
            vcpu = self.domain.xpath('//vcpu')[0]
            vcpu.text = count

    def attach_iso(self, iso):
        """Attach a ISO file as DVDRom drive."""
        raise

    def detach_iso(self):
        """Detach the ISO file in the DVDRom drive."""
        raise

    def set_field(self, key, value):
        """Set a specific field of a Virtual Machine."""
        raise

    def modify_mac(self, macaddr=None, index=0):
        """Modify the MAC address of a Virtual Machine."""
        raise

    def network_index(self):
        """Get the index for the next network interface."""
        raise

    def hostonly(self, nictype="e1000", macaddr=None, adapter=None):
        """Configure hostonly for the Virtual Machine."""
        raise

    def nat(self, nictype="e1000", macaddr=None, adapter=None):
        """Configure NAT for the Virtual Machine."""
        raise

    def hwvirt(self, enable=True):
        """Enable or disable the usage of Hardware Virtualization."""
        raise

    def start_vm(self, visible=False):
        """Start the associated Virtual Machine."""
        raise

    def list_snapshots(self):
        """ Returns a list of snapshots for the specific VMX file """
        raise

    def snapshot(self, label):
        """Take a snapshot of the associated Virtual Machine."""
        raise

    def restore_snapshot(self, label=None):
        """ Revert to the latest snapshot available """
        raise

    def delete_snapshot(self, label, recursive=False):
        raise

    def stopvm(self, powertype="soft"):
        """Stop the associated Virtual Machine."""
        raise

    def remotedisplay(self, port=5901, password=""):
        """ Provides a VNC/RDP interface for GUI communication over the network """
        raise

    def enableparavirt(self):
        raise

    def export(self, filepath):
        raise

