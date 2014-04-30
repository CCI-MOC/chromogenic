"""
ExportManager:

"""

import getopt
import glob
import logging
import os
import subprocess
import sys
import time

from hashlib import md5
from datetime import datetime
from urlparse import urlparse
from xml.dom import minidom

from boto import connect_ec2
from boto.ec2.regioninfo import RegionInfo
from boto.ec2.instance import Instance
from boto.s3.connection import S3Connection, OrdinaryCallingFormat
from boto.exception import S3ResponseError, S3CreateError
from boto.s3.key import Key
from boto.resultset import ResultSet

from euca2ools import Euca2ool, FileValidationError, Util, ConnectionFailed


from django.utils import timezone

from chromogenic.drivers.eucalyptus import ImageManager as EucaImageManager
from chromogenic.drivers.migration import Xen2KVM
from chromogenic.drivers.base import BaseDriver
from chromogenic.boot import add_grub
from chromogenic.common import sed_delete_multi, sed_replace, sed_append
from chromogenic.common import run_command, copy_disk, create_empty_image
from chromogenic.common import mount_image, check_distro
from chromogenic.export import add_virtualbox_support

logger = logging.getLogger(__name__)

class ImageManager(BaseDriver):
    """
    Convienence class that can convert VMs into localized machines for
    Oracle Virtualbox®
    """
    def __init__(self, *args, **kwargs):
        if len(args) == 0 and len(kwargs) == 0:
            raise KeyError("Credentials missing in __init__. ")
    def create_image(self, instance_id, image_name, *args, **kwargs):
        """
        """
        raise Exception("To create a virtualbox image,"
                " pass the ImageManager class and arguments as the destination"
                " fields in a migration")
    def parse_upload_args(self, **kwargs):
        pass
    
    def upload_local_image(self, image_location, image_name, *args, **kwargs):
        """
        Anything that the VBox Export Manager needs to add...
        """
        import ipdb;ipdb.set_trace()
        add_virtualbox_support(mount_point, image_path)
        self.export_image(image_path)
        #Do VBox stuff..


    #def eucalyptus(self, instance_id, vm_name, owner, disk_type='vmdk',
    #               download_dir='/tmp', local_raw_path=None,
    #               harddrive_path=None, appliance_path=None,
    #               no_upload=False, meta_name=None):
    #    """
    #    Note: vm_name is the name you want for your new virtualbox vm (Does not have to be the same!)
    #    """
    #    self.euca_img_manager = EucaImageManager(**self.export_credentials)
    #    #Download the image, then make a bootable RAW copy and install a
    #    # bootloader
    #    if not local_raw_path or not os.path.exists(local_raw_path):
    #        download_dir, local_img_path = self.euca_img_manager.download_instance(
    #            download_dir, instance_id,
    #            meta_name=meta_name)

    #        mount_point = os.path.join(download_dir,'mount/')

    #        try:
    #            mount_image(local_img_path, mount_point)
    #            distro = check_distro(mount_point)
    #        finally:
    #            run_command(['umount', mount_point])

    #        if distro.lower() != 'centos':
    #            #TODO: Get it working for ubuntu
    #            pass#raise Exception("Whoa! This process only works for CentOS machines!")


    #    #    self.euca_img_manager._clean_local_image(local_img_path, mount_point)

    #        Xen2KVM.convert(local_img_path, download_dir)
    #        try:
    #            mount_image(local_img_path, mount_point)
    #            add_virtualbox_support(mount_point, local_img_path)
    #        finally:
    #            run_command(['umount', mount_point])
    def rebuild_disk(local_img_path, ext="raw"):
        #Image is now ready to be placed on a bootable drive, then install grub-legacy
        image_size = self._get_file_size_gb(local_img_path)
        local_raw_path = local_img_path +  "." + ext
        create_empty_image(local_raw_path, ext,
                           image_size+5,  # Add some empty space..
                           bootable=True)
        #copy the data
        copy_disk(old_image=local_img_path,
                  new_image=local_raw_path,
                  download_dir=download_dir)
        #Add grub.
        try:
            mount_image(local_raw_path, mount_point)
            add_grub(mount_point, local_raw_path)
        finally:
            run_command(['umount', mount_point])
        return local_raw_path

    def export_image(local_img_path, upload=False, *args, **kwargs):
        #Convert the image if it was not passed as a kwarg
        harddrive_path = self._create_virtual_harddrive(
                local_raw_path, 'vmdk')

        appliance_path = self._build_new_export_vm(vm_name, harddrive_path)

        #Get the hash of the converted file
        md5sum = self._large_file_hash(appliance_path)
        if not upload:
            return (md5sum, appliance_path)
        ##Archive/Compress/Send the export to S3
        tarfile_name = appliance_path+'.tar.gz'
        self._tarzip_image(tarfile_name, [appliance_path])
        s3_keyname = 'vbox_export_%s_%s' % (instance_id,datetime.now().strftime('%Y%m%d_%H%M%S'))
        url = self._export_to_s3(s3_keyname, tarfile_name)
        return (md5sum, url)

    def _strip_uuid(self, createvm_output):
        import re
        regex = re.compile("UUID: (?P<uuid>[a-zA-Z0-9-]+)")
        r = regex.search(createvm_output)
        uuid = r.groupdict()['uuid']
        return uuid


    def _build_new_export_vm(self, name, harddrive_path, vm_opts={}, distro='Linux'):
        export_dir = os.path.dirname(harddrive_path)
        hostname_out, _ = run_command(['hostname'])
        export_file = os.path.join(export_dir,'%s_%s_%s.ova' % (name, hostname_out.strip(), timezone.now()))
        if os.path.exists(export_file):
            #Remove vm method here..
            pass

        out, err = run_command(['VBoxManage','createvm','--basefolder',
            export_dir, '--name', name, '--ostype', distro, '--register'])
        vm_uuid = self._strip_uuid(out)
        modify_vm_opts = {
            'vram':'16',  # vram <= 8 MB causes poor performance..

            'memory':512,
            'acpi': 'on',
            'ioapic':'on'
        }
        modify_vm_opts.update(vm_opts)
        modify_vm_command = ['VBoxManage','modifyvm', vm_uuid]
        for (k,v) in modify_vm_opts.items():
            modify_vm_command.append('--%s' % k)
            modify_vm_command.append('%s' % v)
        run_command(modify_vm_command)
        run_command(['VBoxManage', 'storagectl', vm_uuid, '--name', 'Hard Drive', '--add', 'sata', '--controller', 'IntelAHCI'])
        run_command(['VBoxManage', 'storageattach', vm_uuid, '--storagectl', 'Hard Drive', '--type', 'hdd', '--medium', harddrive_path, '--port','0','--device','0'])
        run_command(['VBoxManage', 'export', vm_uuid, '--output', export_file])
        return export_file
        
        
    def _get_file_size_gb(self, filename):
        #TODO: Move to export.py
        import math
        byte_size = os.path.getsize(filename)
        one_gb = 1024**3
        gb_size = math.ceil( float(byte_size)/one_gb )
        return int(gb_size)


    #def _export_to_s3(self, keyname, the_file, bucketname='eucalyptus_exports'):
    #    key = self.euca_img_manager._upload_file_to_s3(bucketname, keyname, the_file) #Key matches on basename of file
    #    url = key.generate_url(60*60*24*7) # 7 days from now.
    #    return url

    def _large_file_hash(self, file_path):
        #TODO: Move to export.py
        logger.debug("Calculating MD5 Hash for %s" % file_path)
        md5_hash = md5()
        with open(file_path,'rb') as f:
            for chunk in iter(lambda: f.read(md5_hash.block_size * 128), b''): #b'' == Empty Byte String
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    def _tarzip_image(self, tarfile_path, file_list):
        #TODO: Move to export.py
        import tarfile
        tar = tarfile.open(tarfile_path, "w:gz")
        logger.debug("Creating tarfile:%s" % tarfile_path)
        for name in file_list:
            logger.debug("Tarring file:%s" % name)
            tar.add(name)
        tar.close()

    def _create_virtual_harddrive(self, local_img_path, disk_type):
        if 'vmdk' in disk_type:
            convert_img_path = os.path.splitext(local_img_path)[0] + '.vmdk'
            run_command(['qemu-img', 'convert', local_img_path, '-O', 'vmdk', convert_img_path])
        elif 'vdi' in disk_type:
            raw_img_path = os.path.splitext(local_img_path)[0] + '.raw'
            #Convert to raw if its anything else..
            if '.raw' not in local_img_path:
                run_command(['qemu-img', 'convert', local_img_path, '-O', 'raw', raw_img_path])
            #Convert from raw to vdi
            convert_img_path = os.path.splitext(local_img_path)[0] + '.vdi'
            #NOTE: Must DELETE first!
            run_command(['VBoxManage', 'convertdd',raw_img_path, convert_img_path])
        else:
            convert_img_path = None
            logger.warn("Failed to export. Unknown type: %s" % (disk_type,) )
        return convert_img_path
