import json
import os
import select
import socket
import sys
import glob
import re
import subprocess
from subprocess import PIPE
import struct
import threading
import time
from matrix_client.api import MatrixHttpApi
import requests
from flask import Flask, jsonify, request
import atexit
import base64
import io, urllib
from urlparse import urlparse


#constants
global_config = {}

#
app = Flask(__name__)
minecraft = None
roomsync = {}

class socket_util(object):
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.soc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.soc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.msglist = []
        self.addr = None
        print ("Socket Init Complete")
        self.proc = None
        self.exit = False
        atexit.register(self.close_socket)
        print "Starting Messaging Thread"
        msg_process = threading.Thread(target=self.msg_process)
        msg_process.daemon = True
        msg_process.start()
        
    def msg_process(self):
        raise NotImplementedError("Please Implement this method")
           
    def socket_reset(self):
        self.soc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.soc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.msglist = []
        self.addr = None
            
    def close_socket(self):
        self.soc.close()
            
    def send(self, message):
        #select returns (readable, writable, error) status on the objects passed in the corresponding lists
        r, w, e = select.select([], [self.soc], [], 1)
        #print "w" + str(w)
        if w == []:
            return 1
        string_message = json.dumps(message)
        select.select([], [self.soc], [])
        self.write_int(len(string_message))
        self.write(string_message)
        return 0

    def write_int(self,  integer):
        integer_buf = struct.pack('>i', integer)       
        self.write(integer_buf)

    def write(self, data):
        #socket.bind(address)
        data_len = len(data)
        offset = 0
        while offset != data_len:
            offset += self.soc.send(data[offset:])

    def receive(self):
        r,s,e = select.select([self.soc], [], [], 1)
        #print "r" + str(r)
        if r == []:
            return ""
        message_size = self.read_int()
        if message_size == None:
            self.close_socket()
            return None
        data = self.read(message_size)
        if data == None:
            print "data_none"
            return None
        message = json.loads(data)

        return message

    def read_int(self):
        int_size = struct.calcsize('>i')
        intbuf = self.read(int_size)
        if intbuf == None:
            return None
        return struct.unpack('>i', intbuf)[0]

    def read(self, size):
        data = ""
        while len(data) != size:
            newdata = self.soc.recv(size - len(data))
            if len(newdata) == 0:
               return None
            data = data + newdata
        return data

class MinecraftWrapper(socket_util):
    def __init__(self, host, port):
        super(MinecraftWrapper,self).__init__(host, port)
        print "Starting Wrapper Polling Thread"
        poll_process = threading.Thread(target=self.cli_poll)
        poll_process.daemon = True
        poll_process.start()
        self.socket_reset()
        
    def socket_reset(self):
        super(MinecraftWrapper,self).socket_reset()
        self.soc.connect((self.host, self.port))
        print "Socket Connected"
            
    def exe_mc(self):
        self.proc = subprocess.Popen(sys.argv[1:], shell=True, stdout=PIPE, stdin=PIPE, universal_newlines=True)
        for stdout_line in iter(self.proc.stdout.readline, ""):
            yield stdout_line
        return_code = self.proc.wait()
        if return_code:
            raise subprocess.CalledProcessError(return_code, cmd)
            
    def msg_process(self):
        while(not self.exit):
            try:
                self.proc_monitor()
                status = 1
                if len(self.msglist) > 0:
                    status = self.send(self.msglist[-1])
                rcv = self.receive()
                if rcv != "" and rcv != None:
                        self.msg_handle(rcv)
                if status == 0: self.msglist.pop()
            except Exception as e:
                print e
                self.socket_reset()
        
            
    def msg_handle(self, msg):
        if len(msg) > 0:
            if msg[0] == '/':
                self.proc.stdin.write(msg + '\n')
            else:
                print(msg)
                
    def proc_monitor(self):
        try:
            if self.proc.poll() is not None:
                self.exit = True
                self.close_socket()
                sys.exit(0)
        except:
            print "poll error"
            pass
    
    def cli_poll(self):
        prog = re.compile("^\[(.*)\] \[(.*)\]: <(.*)> (.*)")
        for line in self.exe_mc():
            print(line.rstrip('\n'))
            # regex to get user and text: ^<(.*)> (.*)\n
            result = prog.search(line)
            if result:
                #print("user: " + result.group(3) + " msg: " +result.group(4).rstrip('\n'))
                #msb.send({"user":result.group(3),"msg":result.group(4).rstrip('\n')})
                self.msglist.insert(0, {"user":result.group(3),"msg":result.group(4).rstrip('\n')})
        
class MinecraftServerBridge(socket_util):
    def __init__(self, host, port):
        #starting threads
        print ("Starting Appservice Webserver")
        flask_thread = threading.Thread(target=app.run,kwargs={"port":global_config['bridge_matrixapi_port']})
        flask_thread.daemon = True
        flask_thread.start()
    
        #socket and other init
        super(MinecraftServerBridge,self).__init__(host, port)
        print ("Calling Matrix Api")
        self.api = MatrixHttpApi("http://localhost:8008", token=global_config['as_token'])
        self.user_re = re.compile("(?<=\@).*(?=\:)")
        self.avatar_update_log = {}
        print ("Finished Init")
            
        
    def socket_reset(self):
        super(MinecraftServerBridge,self).socket_reset()
        print "Server Binding to " + self.host + " " + str(self.port)
        self.soc.bind((self.host, self.port))
        print "Server Bound"
        self.soc.listen(1)
        print "Server listen to host"
        self.soc, self.addr = self.soc.accept()
        self.soc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        print "Server accepted connection: " + str(self.addr)
            
        
    def msg_process(self):
        while(True):
            try:
                if len(self.msglist) > 0:
                    status = self.send(self.msglist[-1])
                    if status == 0: self.msglist.pop()
                rcv = self.receive()
                if rcv != "" and rcv != None:
                    self.msg_handle(rcv)
            except Exception as e:
                print e
                self.socket_reset()
        
                    
    def msg_handle(self, msg):
        #for msg, create user and post as user
        #add minecraft user to minecraft channel, if this fails, no big deal
        try:
            print "trying to create id..."
            new_user = "@mc_" + msg['user']
            user_id = new_user + ":" + global_config['server_name']
            self.api.register("m.login.application_service",username =  "mc_" + msg['user'])
        except Exception as e:
            print e
        #for each room we're aware of, post server chat inside. Eventually 1 room should equal 1 server
        for room in roomsync:
            #generate a unique transaction id based on the current time
            txn_id = str(int(time.time() * 1000))
            #attempt to join room
            print "trying to join room as user and as bridge manager"
            self.api._send("POST", '/rooms/'+room+'/join', query_params={"user_id": user_id}, headers={"Content-Type":"application/json"})
            self.api._send("POST", '/rooms/'+room+'/join', headers={"Content-Type":"application/json"})
            #set our display name to something nice
            print "trying to set display name..."
            self.api._send("PUT", '/profile/'+user_id+'/displayname/', content={"displayname":msg["user"]}, query_params={"user_id": user_id}, headers={"Content-Type":"application/json"})
            
            #get our mc skin!!
            #backup: #avatar_url = "https://www.minecraftskinstealer.com/face.php?u="+msg['user']
            #only get this if the user hasn't updated in a long time
            print "Checking if we need to update avatar..."
            if msg['user'] not in self.avatar_update_log.keys() or abs(self.avatar_update_log[msg['user']] - time.time()) > 180:
                self.avatar_update_log[msg['user']] = time.time()
                avatar_url = self.get_mc_skin(msg['user'], user_id)
                if avatar_url:
                    print "avatar_url is " + avatar_url
                    self.api._send("PUT", '/profile/'+user_id+'/avatar_url/', content={"avatar_url":avatar_url}, query_params={"user_id": user_id}, headers={"Content-Type":"application/json"})
                
            #attempt to post in room
            print "Attempting to post in Room"
            self.api._send("PUT", '/rooms/'+room+'/send/m.room.message/' + txn_id, content={"msgtype":"m.text","body":msg["msg"]}, query_params={"user_id": user_id}, headers={"Content-Type":"application/json"})
    
    def get_mc_skin(self, user, user_id):
        print("Getting Minecraft Avatar")
        from PIL import Image
        mojang_info = requests.get('https://api.mojang.com/users/profiles/minecraft/'+user).json() #get uuid
        mojang_info = requests.get('https://sessionserver.mojang.com/session/minecraft/profile/'+mojang_info['id']).json() #get more info from uuid
        mojang_info = json.loads(base64.b64decode(mojang_info['properties'][0]['value']))
        mojang_url = mojang_info['textures']['SKIN']['url']
        #r = requests.get(mojang_url, stream=True)
        #r.raw.decode_content = True # handle spurious Content-Encoding
        file = io.BytesIO(urllib.urlopen(mojang_url).read())
        im = Image.open(file)
        img_head = im.crop((8,8,16,16))
        image_buffer_head = io.BytesIO()
        img_head.save(image_buffer_head, "PNG")
        
        #compare to user's current id so we're not uploading the same pic twice
        #GET /_matrix/client/r0/profile/{userId}/avatar_url
        print "Getting Current Avatar URL"
        curr_url = self.api._send("GET", '/profile/'+user_id+'/avatar_url/', query_params={"user_id": user_id}, headers={"Content-Type":"application/json"})
        upload = True
        if 'avatar_url' in curr_url.keys():
            print "Checking Avatar..."
            file = io.BytesIO(urllib.urlopen(self.api.get_download_url(curr_url['avatar_url'])).read())
            im = Image.open(file)
            image_buffer_curr = io.BytesIO()
            im.save(image_buffer_curr, "PNG")
            if (image_buffer_head.getvalue()) == (image_buffer_curr.getvalue()):
                print "Image Same"
                upload = False
        if upload:
            #upload img
            #POST /_matrix/media/r0/upload
            print "Returning updated avatar"
            print image_buffer_head
            return self.api.media_upload(image_buffer_head.getvalue(), "image/png")["content_uri"]
        else:
            return None
        
         
@app.route("/transactions/<transaction>", methods=["PUT"])
def on_receive_events(transaction):
    print("got event")
    events = request.get_json()["events"]
    for event in events:
        print "User: %s Room: %s" % (event["user_id"], event["room_id"])
        print "Event Type: %s" % event["type"]
        print "Content: %s" % event["content"]
        roomsync[event["room_id"]] = ""
        if event['type'] == 'm.room.message' and \
           event['content']['msgtype'] == 'm.text' and \
           event["user_id"].find("@mc_") == -1:
            
            m_user = minecraft.user_re.search(event["user_id"]).group(0)
            m_cont = event['content']['body']
            minecraft.msglist.insert(0, "/tellraw @a {\"text\":\"<" + m_user + "> " + m_cont + "\",\"insertion\":\"/tellraw @p %s\"}")

    return jsonify({})

@app.route("/rooms/<room>", methods=["GET"])
def on_room(room):
    print "returning: " + str(room)
    return jsonify({})

    
bridge_cfg_skeleton = {"as_token":"", "server_name":"","bridge_mcdata_port":-1, "bridge_matrixapi_port":-1}
wrapper_cfg_skeleton = {"server_name":"","wrapper_mcdata_port":-1,}
def make_config(configfile, server=True):
    if not glob.glob(configfile):
        with open(configfile, 'w') as outfile:
            if server:
                json.dump(bridge_cfg_skeleton, outfile)
            else:
                json.dump(wrapper_cfg_skeleton, outfile)
        print "Please edit {0} and then run again!".format(configfile)
        sys.exit(0)
            
    elif glob.glob(configfile):
        with open(configfile) as config:
            read_config = json.load(config)
        return read_config

if __name__=="__main__":
    if glob.glob("minecraft_server.*"):
        print "Running Minecraft Server Wrapper Mode"
        global_config = make_config("wrapper.json", server=False)
        ip_addr_info = socket.gethostbyname_ex(global_config['server_name'])
        minecraft = MinecraftWrapper(ip_addr_info[2][0], global_config['wrapper_mcdata_port'])
    else:
        print "Running Minecraft Matrix Bridge Mode"
        global_config = make_config("server.json", server=True)
        minecraft = MinecraftServerBridge("localhost", global_config['bridge_mcdata_port'])
    print "All Threads Running"
    cmd = ""
    while(not minecraft.exit):
        if minecraft.proc != None and 'stop' not in cmd:
            cmd = raw_input()
            minecraft.proc.stdin.write(cmd + '\n')
        else:
            time.sleep(1)
    print "Calling exit() in main thread..."
    sys.exit()
        
     
    