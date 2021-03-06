#!/usr/bin/env python
# This file includes the common operations with eFuses for chips
#
# Copyright (C) 2020 Espressif Systems (Shanghai) PTE LTD
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51 Franklin
# Street, Fifth Floor, Boston, MA 02110-1301 USA.
from __future__ import division, print_function

import argparse
import esptool
from . import base_fields
from . import util
from bitstring import BitString


def add_common_commands(subparsers, efuses):
    class ActionEfuseValuePair(argparse.Action):
        def __init__(self, option_strings, dest, nargs=None, **kwargs):
            self._nargs = nargs
            self._choices = kwargs.get("efuse_choices")
            self.efuses = kwargs.get("efuses")
            del kwargs["efuse_choices"]
            del kwargs["efuses"]
            super(ActionEfuseValuePair, self).__init__(option_strings, dest, nargs=nargs, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            def check_efuse_name(efuse_name, efuse_list):
                if efuse_name not in self._choices:
                    raise esptool.FatalError("Invalid the efuse name '{}'. Available the efuse names: {}".format(efuse_name, self._choices))

            efuse_value_pairs = {}
            if len(values) > 1:
                if len(values) % 2:
                    raise esptool.FatalError("The list does not have a valid pair (name value) {}".format(values))
                for i in range(0, len(values), 2):
                    efuse_name, new_value = values[i:i + 2:]
                    check_efuse_name(efuse_name, self._choices)
                    check_arg = base_fields.CheckArgValue(self.efuses, efuse_name)
                    efuse_value_pairs[efuse_name] = check_arg(new_value)
            else:
                # For the case of compatibility, when only the efuse_name is given
                # The fields with 'bitcount' and 'bool' types can be without new_value arg
                efuse_name = values[0]
                check_efuse_name(efuse_name, self._choices)
                check_arg = base_fields.CheckArgValue(self.efuses, efuse_name)
                efuse_value_pairs[efuse_name] = check_arg(None)
            setattr(namespace, self.dest, efuse_value_pairs)

    burn = subparsers.add_parser('burn_efuse', help='Burn the efuse with the specified name')
    burn.add_argument('name_value_pairs', help='Name of efuse register and New value pairs to burn',
                      action=ActionEfuseValuePair,
                      nargs="+",
                      metavar="[EFUSE_NAME VALUE] [{} VALUE".format(" VALUE] [".join([e.name for e in efuses.efuses])),
                      efuse_choices=[e.name for e in efuses.efuses],
                      efuses=efuses)

    read_protect_efuse = subparsers.add_parser('read_protect_efuse', help='Disable readback for the efuse with the specified name')
    read_protect_efuse.add_argument('efuse_name', help='Name of efuse register to burn', nargs="+",
                                    choices=[e.name for e in efuses.efuses if e.read_disable_bit is not None])

    write_protect_efuse = subparsers.add_parser('write_protect_efuse', help='Disable writing to the efuse with the specified name')
    write_protect_efuse.add_argument('efuse_name', help='Name of efuse register to burn', nargs="+",
                                     choices=[e.name for e in efuses.efuses if e.write_disable_bit is not None])

    burn_block_data = subparsers.add_parser('burn_block_data', help="Burn non-key data to EFUSE blocks. "
                                            "(Don't use this command to burn key data for Flash Encryption or Secure Boot, " +
                                            "as the byte order of keys is swapped (use burn_key)).")
    add_force_write_always(burn_block_data)
    burn_block_data.add_argument('--offset', '-o', help='Byte offset in the efuse block', type=int, default=0)
    burn_block_data.add_argument('block', help='Efuse block to burn.', action='append', choices=efuses.BURN_BLOCK_DATA_NAMES)
    burn_block_data.add_argument('datafile', help='File containing data to burn into the efuse block', action='append', type=argparse.FileType('rb'))
    for _ in range(0, len(efuses.BURN_BLOCK_DATA_NAMES)):
        burn_block_data.add_argument('block',  help='Efuse block to burn.', metavar="BLOCK", nargs="?", action='append',
                                     choices=efuses.BURN_BLOCK_DATA_NAMES)
        burn_block_data.add_argument('datafile', nargs="?", help='File containing data to burn into the efuse block',
                                     metavar="DATAFILE", action='append', type=argparse.FileType('rb'))

    set_bit_cmd = subparsers.add_parser('burn_bit', help="Burn bit in the efuse block.")
    add_force_write_always(set_bit_cmd)
    set_bit_cmd.add_argument('block', help='Efuse block to burn.', choices=efuses.BURN_BLOCK_DATA_NAMES)
    set_bit_cmd.add_argument('bit_number', help='Bit number in the efuse block [0..BLK_LEN-1]', nargs="+", type=int)

    subparsers.add_parser('adc_info', help='Display information about ADC calibration data stored in efuse.')


def add_force_write_always(p):
    p.add_argument('--force-write-always', help="Write the efuse even if it looks like it's already been written, or is write protected. " +
                   "Note that this option can't disable write protection, or clear any bit which has already been set.", action='store_true')


def dump(esp, efuses, args):
    """ Dump raw efuse data registers """
    # Using --debug option allows to print dump.
    # Nothing to do here. The log will be print during EspEfuses.__init__() in self.read_blocks()
    if args.file_name:
        # save dump to the file
        for block in efuses.blocks:
            file_dump_name = args.file_name
            place_for_index = file_dump_name.find(".bin")
            file_dump_name = file_dump_name[:place_for_index] + str(block.id) + file_dump_name[place_for_index:]
            print(file_dump_name)
            with open(file_dump_name, "wb") as f:
                block.get_bitstring().byteswap()
                block.get_bitstring().tofile(f)


def burn_efuse(esp, efuses, args):
    def print_attention(blocked_efuses_after_burn):
        if len(blocked_efuses_after_burn):
            print("    ATTENTION! This BLOCK uses NOT the NONE coding scheme and after 'BURN', these efuses can not be burned in the feature:")
            for i in range(0, len(blocked_efuses_after_burn), 5):
                print("              ", "".join("{}".format(blocked_efuses_after_burn[i:i + 5:])))

    efuse_name_list = [name for name in args.name_value_pairs.keys()]
    burn_efuses_list = [efuses[name] for name in efuse_name_list]
    old_value_list = [efuses[name].get_raw() for name in efuse_name_list]
    new_value_list = [value for value in args.name_value_pairs.values()]
    util.check_duplicate_name_in_list(efuse_name_list)

    attention = ""
    print("The efuses to burn:")
    for block in efuses.blocks:
        burn_list_a_block = [e for e in burn_efuses_list if e.block == block.id]
        if len(burn_list_a_block):
            print("  from BLOCK%d" % (block.id))
            for field in burn_list_a_block:
                print("     - %s" % (field.name))
                if efuses.blocks[field.block].get_coding_scheme() != efuses.CODING_SCHEME_NONE:
                    using_the_same_block_names = [e.name for e in efuses if e.block == field.block]
                    wr_names = [e.name for e in burn_list_a_block]
                    blocked_efuses_after_burn = (list(set(using_the_same_block_names) ^ set(wr_names)))
                    attention = " (see 'ATTENTION!' above)"
            if attention:
                print_attention(blocked_efuses_after_burn)

    print("\nBurning efuses{}:".format(attention))
    for efuse, new_value in zip(burn_efuses_list, new_value_list):
        print("\n    - '{}' ({}) {} -> {}".format(efuse.name, efuse.description, efuse.get_bitstring(), efuse.convert_to_bitstring(new_value)))
        efuse.save(new_value)

    efuses.burn_all()

    print("Checking efuses...")
    raise_error = False
    for efuse, old_value, new_value in zip(burn_efuses_list, old_value_list, new_value_list):
        if not efuse.is_readable():
            print("Efuse %s is read-protected. Read back the burn value is not possible." % efuse.name)
        else:
            new_value = efuse.convert_to_bitstring(new_value)
            burned_value = efuse.get_bitstring()
            if burned_value != new_value:
                print(burned_value, "->", new_value, "Efuse %s failed to burn. Protected?" % efuse.name)
                raise_error = True
    if raise_error:
        raise esptool.FatalError("The burn was not successful.")
    else:
        print("Successful")


def read_protect_efuse(esp, efuses, args):
    util.check_duplicate_name_in_list(args.efuse_name)

    for efuse_name in args.efuse_name:
        efuse = efuses[efuse_name]
        if not efuse.is_readable():
            print("Efuse %s is already read protected" % efuse.name)
            return
        else:
            # make full list of which efuses will be disabled (ie share a read disable bit)
            all_disabling = [e for e in efuses if e.read_disable_bit == efuse.read_disable_bit]
            names = ", ".join(e.name for e in all_disabling)
            print("Permanently read-disabling efuse%s %s" % ("s" if len(all_disabling) > 1 else "", names))
            efuse.disable_read()
    efuses.burn_all()

    print("Checking efuses...")
    raise_error = False
    for efuse_name in args.efuse_name:
        efuse = efuses[efuse_name]
        if efuse.is_readable():
            print("Efuse %s is not read-protected." % efuse.name)
            raise_error = True
    if raise_error:
        raise esptool.FatalError("The burn was not successful.")
    else:
        print("Successful")


def write_protect_efuse(esp, efuses, args):
    util.check_duplicate_name_in_list(args.efuse_name)
    for efuse_name in args.efuse_name:
        efuse = efuses[efuse_name]
        if not efuse.is_writeable():
            print("Efuse %s is already write protected" % efuse.name)
        else:
            # make full list of which efuses will be disabled (ie share a write disable bit)
            all_disabling = [e for e in efuses if e.write_disable_bit == efuse.write_disable_bit]
            names = ", ".join(e.name for e in all_disabling)
            print("Permanently write-disabling efuse%s %s" % ("s" if len(all_disabling) > 1 else "", names))
            efuse.disable_write()
    efuses.burn_all()

    print("Checking efuses...")
    raise_error = False
    for efuse_name in args.efuse_name:
        efuse = efuses[efuse_name]
        if efuse.is_writeable():
            print("Efuse %s is not write-protected." % efuse.name)
            raise_error = True
    if raise_error:
        raise esptool.FatalError("The burn was not successful.")
    else:
        print("Successful")


def burn_block_data(esp, efuses, args):
    block_name_list = args.block[0:len([name for name in args.block if name is not None]):]
    datafile_list = args.datafile[0:len([name for name in args.datafile if name is not None]):]
    efuses.force_write_always = args.force_write_always

    util.check_duplicate_name_in_list(block_name_list)
    if args.offset and len(block_name_list) > 1:
        raise esptool.FatalError("The 'offset' option is not applicable when a few blocks are passed. With 'offset', should only one block be used.")
    else:
        offset = args.offset
        if offset:
            num_block = efuses.get_index_block_by_name(block_name_list[0])
            block = efuses.blocks[num_block]
            num_bytes = block.get_block_len()
            if offset >= num_bytes:
                raise esptool.FatalError("Invalid offset: the block%d only holds %d bytes." % (block.id, num_bytes))
    if len(block_name_list) != len(datafile_list):
        raise esptool.FatalError("The number of block_name (%d) and datafile (%d) should be the same." % (len(block_name_list), len(datafile_list)))

    for block_name, datafile in zip(block_name_list, datafile_list):
        num_block = efuses.get_index_block_by_name(block_name)
        block = efuses.blocks[num_block]
        data = datafile.read()
        num_bytes = block.get_block_len()
        if offset != 0:
            data = (b'\x00' * offset) + data
            data = data + (b'\x00' * (num_bytes - len(data)))
        if len(data) != num_bytes:
            raise esptool.FatalError("Data does not fit: the block%d size is %d bytes, data file is %d bytes, offset %d" %
                                     (block.id, num_bytes, len(data), offset))
        print("[{:02}] {:20} size={:02} bytes, offset={:02} - > [{}].".format(block.id, block.name, len(data), offset, util.hexify(data, " ")))
        block.save(data)
    efuses.burn_all()
    print("Successful")


def burn_bit(esp, efuses, args):
    num_block = efuses.get_index_block_by_name(args.block)
    block = efuses.blocks[num_block]
    data_block = BitString(block.get_block_len() * 8)
    data_block.set(0)
    try:
        data_block.set(True, args.bit_number)
    except IndexError:
        raise esptool.FatalError("%s has bit_number in [0..%d]" % (args.block, data_block.len - 1))
    data_block.reverse()
    print("bit_number:   [%-03d]........................................................[0]" % (data_block.len - 1))
    print("BLOCK%-2d   :" % block.id, data_block)
    block.print_block(data_block, "regs_to_write", debug=True)
    block.save(data_block.bytes[::-1])
    efuses.burn_all()
    print("Successful")
