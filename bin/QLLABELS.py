#!/usr/bin/env python3
# vim: expandtab shiftwidth=4 tabstop=4
#
# RaceDB QLLabels Python Script
#
# Usage
# In the RaceDB System Info Edit screen:
#
#   Cmd used to print Bib Tag (parameter is PDF file)
#
#       [  /home/RaceDB/scripts/QLLABELS.py $1 ]
# 
# This script will convert the PDF file to Brother Raster file(s) and 
# send the Raster file(s) to a Brother QL style label printer or to 
# the qlmuxd printer spooler.
#
# N.b. the file name is provided as a parameters, the PDF data is also
# provided on stdin.
# 
# Sending the files directly to the printers on port 9100 works, but 
# only when # there is only a single person using RaceDB. Multiple prints 
# to a QL printer will result in over lapping labels.
#
# The qlmuxd program manages pools of QL printers and will spool
# the data allowing multiple people to print to them, with support
# for different printers for the different antennas and fall-over
# support if (when) the printers are not available (typically when
# they run out of labels. The qlmuxd program takes the same raster
# file data that would be sent to the printers on port 9100, but
# uses ports 910N to allow us to specify which pool of printers
# to use.
#
# This script will get labels printed (either directly or via qlmuxd) 
# far faster than CUPS using the standard Brother QL support files.
#
# This script will:
#
#   1. Convert $1 to $1-$PAGENO.png for each page in the PDF file
#   2. Convert each page to $1-$PAGENO.rast
#   3. Equivalent of cat $1-*.rast | netcat $PRINTER_HOST $PRINTER_PORT
#
#
# The argument provided is the file name which contains information about what is to 
# be printed. E.g.:
#
#       230489203498023809_bib-356_port-8000_antenna-2_type-Frame.pdf
#
# "type" is one of Frame, Body, Shoulder or Emergency.
# "port" is the RaceDB server port.
# "antenna" is the antenna of the user.
#
# The combination of server port and antenna allows different printers to 
# be used for different registration stations. The server port refers to
# the TCP port that the server responds to, e.g. 8000 or 8001 etc.
#

import sys
import os
import socket
import subprocess
import jsoncfg
import json
import io
import traceback

import pdf2image
from pdf2image import convert_from_path

import sys
import datetime
getTimeNow = datetime.datetime.now

def usage(s):
    log('Usage: QLLABELS.py 130489203498023809_bib-719_port-8000_antenna-1_type-Frame.pdf')
    log('       %s' % (s))
    exit(1)

def log(s):
        print('%s %s' % (getTimeNow().strftime('%H:%M:%S'), s.rstrip()), file=sys.stderr)

# get the filename provided as the first arguement
#
try:
    fname = os.path.basename(sys.argv[1])
except:
    usage('No filename arguement')


# parse qlabels.cfg to get:
#
#   label sizes dictionary - map types to size, small or large
#   label pools dictionary - map port-antenna to pool for small and large to a pool name
#   label printers dictionary - hostname and port to use for a each pool
#
cfgs = ['/usr/local/etc/qllabels.cfg', 'qllabels.cfg']
config = None
for c in cfgs:
    try:
        config = jsoncfg.load_config(c)
        break
    except Exception as e:
        log('QLLABELS: error cannot open: %s %s' % (c, e))
        continue
if config is None:
    log('QLLABELS: error cannot open either: %s' % cfgs)
    exit(1)

# get the dictionaries
sizes = config.QLLABELS_Sizes()
pools = config.QLLABELS_Pools()
printers = config.QLLABELS_Printers()

#print('sizes: %s' % (sizes))
#print('printers: %s' % (printers))
#print('pools: %s' % (pools))

# Split file name apart to get information about the label.
#   bib, port, antenna and type parameters
# e.g:
#   230489203498023809_bib-719_port-8000_antenna-0_type-Frame.pdf
#
# Numeric fields are converted to numbers to allow comparisons like params['antenna'] == 1
params = { k:(int(v) if v.isdigit() else v) for k, v in (p.split('-') for p in os.path.splitext(fname)[0].split('_')[1:] if '-' in p ) }
#print('params: %s' % (params))

try:
    size = sizes[params['type']]
except:
    usage('Do not understand type-%s' % (params['type']))

poolMatch = "%s-%d" % (params['port'], params['antenna'])
try:
    pool = pools[poolMatch]
except:
    usage('Do not understand %s' % (poolMatch))

try:
    printerName = pool[size]
except:
    usage('Do not understand printerName %s' % (printerName))

try:
    printer = printers[printerName]
except:
    usage('Cannot find printerName %s' % (printerName))

imagesize = {
    '62': (1109, 696),
    '62x100': (1109, 696),
    '102': (1660, 1164),
    '102x152': (1660, 1164),
}

try:
    hostname = printer['hostname']
    port = printer['port']
    model = printer['model']
    labelsize = printer['labelsize']
except:
    usage('Cannot find one of hostname, port, model, labelsize: %s' % (printer))
    usage()


#print('hostname: %s port: %d model: %s labelsize: %s imagesize: %s' % (hostname, port, model, labelsize, imagesize[labelsize],))


# convert PDF to PNG images using pdf2image (poppler), data from stdin,
# then save each image separately as a png.
#
#images = convert_from_path('/dev/stdin', size=(1109, 696), dpi=280, grayscale=True)
#images = convert_from_path('/dev/stdin', size=(1660, 1164), dpi=280, grayscale=True)

# convert pdf from stdin into list of pillow images
images = convert_from_path('/dev/stdin', size=imagesize[labelsize], dpi=280, grayscale=True)

#last = 0
#for index, image in enumerate(images):
#    pngfile = f'/tmp/{fname}-{index}.png'
#    print(pngfile)
#    image.save(pngfile)
#    last = index

# convert PNG images to Brother Raster file, Note we use --no-cut for 0..N-1, 
# the last file will have a cut so that multiple labels will be kept together.
#

last = len(images)
#print('brother_ql tcp://%s:%s last: %s' % (hostname, port, last), file=sys.stderr)
args_base = [ 'brother_ql', '--printer', f"tcp://{hostname}:{port}",
        '--model', model, 'print', '--rotate', '90', '--label', labelsize, 
        ]
for index, image in enumerate(images):
    pngfile = f'/tmp/{fname}-{index}.png'
    #print(pngfile, file=sys.stderr))
    image.save(pngfile)
    args = args_base.copy()
    if index < (last -1):
        args.append('--no-cut')
    args.append(pngfile)
    try:
        subprocess.run(
            args,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
          ) ;
    except Exception as e:
        print('exception: e: %s' % (e), file=sys.stderr)
        print('traceback: %s' % (traceback.format_exc(),))
        #log('brother_ql_create exception: %s' % (e))
        #exit(1)
    
    os.remove(pngfile)

