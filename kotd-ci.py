#!/usr/bin/env python3

# Copyright (C) 2018 SUSE LLC
#
# This program is free software; you can reconfig["distri"]bute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either config["version"] 2 of the License, or
# (at your option) any later config["version"].
#
# This program is config["distri"]buted in the hope that it will be useful,
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
from email.mime.text import MIMEText
from subprocess import Popen, PIPE



# for IBS and openqa.suse.de
config = {}
config["rabbit_server"] = "amqps://suse:suse@rabbit.suse.de:5671/"
config["amqp_obskey"]='suse.obs.package.build_success'
config["amqp_openqakey"]='suse.openqa.job.done'
config["openqa_url"]='openqa.suse.de'

config["distri"] = 'sle'
config["version"] = '12-SP4'
config["flavor"]='Server-DVD'

# add the groups for different distros/config["version"]s
pending_jobs = 0
group_ids_kotd = {}
group_ids_kotd['sle12-SP4'] = 192

group_ids_kernel = {}
group_ids_kernel['sle12-SP4'] = '155'


current_jobs = {}

class openqa_job:
    job_id = 0
    group_id = 0
    distri = 'sle'
    version = 0
    arch = 'x86_64'
    flavor = 'Server-DVD'
    result = ""
    done = 0
    regression = 0

    def __init__(self, job_id, group_id, distri, version, arch, flavor):
        self.job_id = job_id
        self.group_id = group_id
        self.version = version
        self.distri = distri
        self.arch = arch
        self.flavor = flavor

    def set_result(self, result):
        if not result['id'] == self.job_id:
            return
        self.result = result
        self.done = 1

    # serialize the result into a string
    def __str__(self):
        string = ""
        if self.done == 0:
            return "Job " + self.job_id + " is not yet done"

        r = requests.get("https://" + config['openqa_url'] + "/tests/" + str(self.result['id']) + "/ajax.json")
        data = json.loads(r.text)
        string.append("Test: " + data["data"][0]["name"] + " " + data["data"][0]["result"] + "\n")
        string.append("    previously failed modules: " + data["data"][0]["failedmodules"].as_string() + "\n")
        for module in data["data"][0]["failedmodules"]:
            string.append("   Failed module: " + module)
            if module not in data["data"][1]["failedmodules"]:
                string.append(": new regression!")
                regression = 1
        string.append("\n")
        return string

def send_report():
    regression = ""
    body = "Test report for " + config["distri"] + config["version"] + "\n\n"
    for job in current_jobs:
        body.append(job)
        if job.regression == 1:
            regression = " [new regression(s)]"

    msg = MIMEText(body)
    msg["From"] = "mmoese@suse.de"
    msg["To"] = "mmoese@suse.de"
    msg["Subject"] = "KOTD report for " + config["distri"] + config["version"] + regression
    p = Popen(["/usr/sbin/sendmail", "-t", "-oi"], stdin=PIPE, universal_newlines=True)
    p.communicate(msg.as_string())


def consume_openqa(body):
    result = json.loads(body)

    if result['group_id'] != group_ids_kotd[config["distri"] + config["version"]]:
        return

    current_jobs[result['id']].set_result(result)

    for job in current_jobs:
        if current_jobs[job].done == 0:
            return

    send_report()
       
def get_latest_build(kernel_group):
    params = {}
    params['groupid'] = kernel_group
    tmp = client.openqa_request('GET', 'jobs', params)['jobs']
    results = sorted(tmp, key = lambda k: k['settings']['BUILD'], reverse = True)
    return results[0]['settings']['BUILD']


def trigger_kotd(distri, version, arch, flavor):
    ts = int(time.time())
    build = get_latest_build(group_ids_kernel[config["distri"] + config["version"]])
    params = {}
    params['DISTRI'] = distri
    params['VERSION'] = version
    params['ARCH'] = arch
    params['FLAVOR'] = flavor
    params['_GROUP_ID'] = group_ids_kotd[config["distri"]+config["version"]]

    #construct a new build number to not hide/overwrite old results
    params['BUILD'] = build + "_" + str(ts)
    params['HDD_1'] = config["distri"] + "-" + config["version"] + "-" + arch + "-" + build + "-" + config["flavor"] + "@64bit-with-ltp.qcow2"

    res = client.openqa_request('POST', 'isos', params)
    print(res)
#    pending_jobs = int(res['count'])
#    print("posted ", pending_jobs, " jobs")

    for job_id in res['ids']:
        current_jobs[job_id] = openqa_job(job_id, params['_GROUP_ID'], params['DISTRI'], params['VERSION'], params['ARCH'], params['FLAVOR'])

def consume_ibs(body):
    result = json.loads(body)

    if result['project'] != 'Devel:Kernel:' + config["distri"].upper() + config["version"] or result['package'] != 'kernel-default':
        return

    # TODO: support other arches!
    if not result["arch"] == 'x86_64':
        return

    trigger_kotd(config["distri"], config["version"], result["arch"], config["flavor"])    

# AMQP callback function for IBS and openQA events
def callback(ch, method, properties, body):
    topic = method.routing_key
    if topic == config["amqp_obskey"]:
        consume_ibs(body)
    elif  topic == config["amqp_openqakey"]:
        consume_openqa(body)


def start_amqp():
    pending_jobs = 0
    connection = pika.BlockingConnection(pika.URLParameters(config["rabbit_server"]))
    channel = connection.channel()

    channel.exchange_declare(exchange='pubsub', exchange_type='topic', passive=True, durable=True)

    result = channel.queue_declare(exclusive=True)
    queue_name = result.method.queue

    channel.queue_bind(exchange='pubsub', queue=queue_name,routing_key=config["amqp_obskey"])
    channel.queue_bind(exchange='pubsub', queue=queue_name,routing_key=config["amqp_openqakey"])

    channel.basic_consume(callback, queue=queue_name, no_ack=True)

    channel.start_consuming()



client = OpenQA_Client(server=config["openqa_url"])
print(' [*] Waiting for SLE12-SP4 kernel builds. Abort with CTRL+C')
start_amqp()
