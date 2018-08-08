#!/usr/bin/env python3

# Copyright (C) 2018 SUSE LLC
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Michael Moese <mmoese@suse.de>

import pika
import sys
import json
import re
import time
import requests
from openqa_client.client import OpenQA_Client

pending_jobs = 0

group_ids_kotd = {}
group_ids_kotd['sle12-SP4'] = 192

group_ids_kernel = {}
group_ids_kernel['sle12-SP4'] = '155'

# for IBS and openqa.suse.de
rabbit_server="amqps://suse:suse@rabbit.suse.de:5671/"
amqp_obskey='suse.obs.package.build_success'
amqp_openqakey='suse.openqa.job.done'
openqa_url='openqa.suse.de'

distri = 'sle'
version = '12-SP4'
flavor='Server-DVD'

def consume_openqa(body):
    result = json.loads(body)

    if result['group_id'] != group_ids_kotd[distri + "-" + version]:
        return

    pending_jobs = pending_jobs - 1
    r = requests.get("https://openqa.suse.de/tests/" + str(result['id']) + "/ajax.json")
    data = json.loads(r.text)
   
    print("Failed modules: ", data["data"][0]["failedmodules"])

    for module in data["data"][0]["failedmodules"]:
        if module not in data["data"][1]["failedmodules"]:
            print("Regression: ", module)
        
    if pending_jobs == 0:
        print("done")

def get_latest_build(kernel_group):
    params = {}
    params['groupid'] = kernel_group
    tmp = client.openqa_request('GET', 'jobs', params)['jobs']
    results = sorted(tmp, key = lambda k: k['settings']['BUILD'], reverse = True)
    return results[0]['settings']['BUILD']

def trigger_kotd(distri, version, arch, flavor):
    ts = int(time.time())
    build = get_latest_build(group_ids_kernel[distri + version])
    params = {}
    params['DISTRI'] = distri
    params['VERSION'] =  version
    params['ARCH'] = arch
    params['FLAVOR'] = flavor 
    params['_GROUP_ID'] = group_ids_kotd[distri+version]

    #construct a new build number to not hide/overwrite old results
    params['BUILD'] = build + "_" + str(ts)
    params['HDD_1'] = distri + "-" + version + "-" + arch + "-" + build + "-" + flavor + "@64bit-with-ltp.qcow2"

    res = client.openqa_request('POST', 'isos', params)
    pending_jobs = res['count']
    print("posted ", pending_jobs, " jobs")

def consume_ibs(body):
    result = json.loads(body)

    if result['project'] != 'Devel:Kernel:' + distri.upper() + version or result['package'] != 'kernel-default':
        return

    # TODO: support other arches!
    if not result["arch"] == 'x86_64':
        return

    trigger_kotd(distri, version, result["arch"], flavor)    

# AMQP callback function for IBS and openQA events
def callback(ch, method, properties, body):
    topic = method.routing_key
    if topic == amqp_obskey:
        consume_ibs(body)
    elif  topic == amqp_openqakey:
        consume_openqa(body)


pending_jobs = 0
client = OpenQA_Client(server=openqa_url)
print(' [*] Waiting for SLE12-SP4 kernel builds. Abort with CTRL+C')

def start_amqp():
    connection = pika.BlockingConnection(pika.URLParameters(rabbit_server))
    channel = connection.channel()

    channel.exchange_declare(exchange='pubsub', exchange_type='topic', passive=True, durable=True)

    result = channel.queue_declare(exclusive=True)
    queue_name = result.method.queue

    channel.queue_bind(exchange='pubsub', queue=queue_name,routing_key=amqp_obskey)
    channel.queue_bind(exchange='pubsub', queue=queue_name,routing_key=amqp_openqakey)

    channel.basic_consume(callback, queue=queue_name, no_ack=True)
    trigger_kotd('sle', '12-SP4', 'x86_64', 'Server-DVD')

    channel.start_consuming()

start_amqp()
