#!/usr/bin/env python
# coding:utf-8

import urlparse
import os
import cgi
import time
import hashlib

from xlog import getLogger
import threading
xlog = getLogger("x_tunnel")

import simple_http_server
import global_var as g
import proxy_session
from cloudflare_front import web_control as cloudflare_web
#from heroku_front import web_control as heroku_web
from front_dispatcher import all_fronts

current_path = os.path.dirname(os.path.abspath(__file__))
root_path = os.path.abspath(os.path.join(current_path, os.pardir, os.pardir))
web_ui_path = os.path.join(current_path, os.path.pardir, "web_ui")


class ControlHandler(simple_http_server.HttpServerHandler):
    def __init__(self, client_address, headers, command, path, rfile, wfile):
        self.client_address = client_address
        self.headers = headers
        self.command = command
        self.path = path
        self.rfile = rfile
        self.wfile = wfile

    def do_GET(self):
        path = urlparse.urlparse(self.path).path
        if path == "/log":
            return self.req_log_handler()
        elif path == "/debug":
            data = g.session.status()
            return self.send_response('text/html', data)
        elif path == "/info":
            return self.req_info_handler()
        elif path == "/get_history":
            return self.req_get_history_handler()
        elif path == "/status":
            return self.req_status()
        elif path.startswith("/cloudflare_front/"):
            path = self.path[17:]
            controler = cloudflare_web.ControlHandler(self.client_address,
                             self.headers,
                             self.command, path,
                             self.rfile, self.wfile)
            controler.do_GET()
        else:
            xlog.warn('Control Req %s %s %s ', self.address_string(), self.command, self.path)

    def do_POST(self):
        xlog.debug('x-tunnel web_control %s %s %s ', self.address_string(), self.command, self.path)
        try:
            ctype, pdict = cgi.parse_header(self.headers.getheader('content-type'))
            if ctype == 'multipart/form-data':
                self.postvars = cgi.parse_multipart(self.rfile, pdict)
            elif ctype == 'application/x-www-form-urlencoded':
                length = int(self.headers.getheader('content-length'))
                self.postvars = urlparse.parse_qs(self.rfile.read(length), keep_blank_values=1)
            else:
                self.postvars = {}
        except:
            self.postvars = {}

        path = urlparse.urlparse(self.path).path
        if path == '/login':
            return self.req_login_handler()
        elif path == "/logout":
            return self.req_logout_handler()
        elif path == "/register":
            return self.req_login_handler()
        elif path == "/order":
            return self.req_order_handler()
        elif path == "/transfer":
            return self.req_transfer_handler()
        elif path.startswith("/cloudflare_front/"):
            path = path[17:]
            controler = cloudflare_web.ControlHandler(self.client_address,
                                                      self.headers,
                                                      self.command, path,
                                                      self.rfile, self.wfile)
            controler.do_POST()
        else:
            xlog.info('%s "%s %s HTTP/1.1" 404 -', self.address_string(), self.command, self.path)
            return self.send_not_found()

    def req_log_handler(self):
        req = urlparse.urlparse(self.path).query
        reqs = urlparse.parse_qs(req, keep_blank_values=True)
        data = ''

        if reqs["cmd"]:
            cmd = reqs["cmd"][0]
        else:
            cmd = "get_last"

        if cmd == "get_last":
            max_line = int(reqs["max_line"][0])
            data = xlog.get_last_lines(max_line)
        elif cmd == "get_new":
            last_no = int(reqs["last_no"][0])
            data = xlog.get_new_lines(last_no)
        else:
            xlog.error('xtunnel log cmd:%s', cmd)

        mimetype = 'text/plain'
        self.send_response(mimetype, data)

    def req_info_handler(self):
        if len(g.config.login_account) == 0 or len(g.config.login_password) == 0:
            return self.response_json({
                "res": "logout"
            })

        if proxy_session.center_login_process:
            return self.response_json({
                "res": "login_process"
            })

        req = urlparse.urlparse(self.path).query
        reqs = urlparse.parse_qs(req, keep_blank_values=True)

        force = False
        if 'force' in reqs:
            xlog.debug("req_info in force")
            force = 1

        time_now = time.time()
        if force or time_now - g.last_refresh_time > 3600 or \
                (g.last_api_error.startswith("status:") and (time_now - g.last_refresh_time > 30)):
            xlog.debug("x_tunnel force update info")
            g.last_refresh_time = time_now

            threading.Thread(target=proxy_session.login_process).start()

            return self.response_json({
                "res": "login_process"
            })

        if len(g.last_api_error) and g.last_api_error != 'balance not enough':
            res_arr = {
                "res": "fail",
                "login_account": "%s" % (g.config.login_account),
                "reason": g.last_api_error
            }
        else:
            res_arr = {
                "res": "success",
                "login_account": "%s" % (g.config.login_account),
                "balance": "%f" % (g.balance),
                "quota": "%d" % (g.quota),
                "quota_list": g.quota_list,
                "traffic": g.session.traffic,
                "last_fail": g.last_api_error
            }
        self.response_json(res_arr)

    def req_login_handler(self):
        def check_email(email):
            import re
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                return False
            else:
                return True

        username    = str(self.postvars['username'][0])
        password    = str(self.postvars['password'][0])
        is_register = int(self.postvars['is_register'][0])

        pa = check_email(username)
        if not pa:
            return self.response_json({
                "res": "fail",
                "reason": "Invalid email."
            })
        elif len(password) < 6:
            return self.response_json({
                "res": "fail",
                "reason": "Password needs at least 6 charactors."
            })

        if password == "_HiddenPassword":
            if username == g.config.login_account and len(g.config.login_password):
                password_hash = g.config.login_password
            else:

                res_arr = {
                    "res": "fail",
                    "reason": "account not exist"
                }
                return self.response_json(res_arr)
        else:
            password_hash = str(hashlib.sha256(password).hexdigest())

        res, reason = proxy_session.request_balance(username, password_hash, is_register, update_server=True)
        if res:
            g.config.login_account  = username
            g.config.login_password = password_hash
            g.config.save()
            res_arr = {
                "res": "success",
                "balance": float(g.balance)
            }
            g.last_refresh_time = time.time()
            g.session.start()
        else:
            res_arr = {
                "res": "fail",
                "reason": reason
            }

        return self.response_json(res_arr)

    def req_logout_handler(self):
        g.config.login_account = ""
        g.config.login_password = ""
        g.config.save()

        g.session.stop()

        return self.response_json({"res": "success"})

    def req_order_handler(self):
        product = self.postvars['product'][0]
        if product != 'x_tunnel':
            xlog.warn("x_tunnel order product %s not support", product)
            return self.response_json({
                "res": "fail",
                "reason": "product %s not support" % product
            })

        plan = self.postvars['plan'][0]
        if plan not in ["quarterly", "yearly"]:
            xlog.warn("x_tunnel order plan %s not support", plan)
            return self.response_json({
                "res": "fail",
                "reason": "plan %s not support" % plan
            })

        res, info = proxy_session.call_api("/order", {
            "account": g.config.login_account,
            "password": g.config.login_password,
            "product": "x_tunnel",
            "plan": plan
        })
        if not res:
            xlog.warn("order fail:%s", info)
            threading.Thread(target=proxy_session.update_quota_loop).start()
            return self.response_json({"res": "fail", "reason": info})

        self.response_json({"res": "success"})

    def req_transfer_handler(self):
        to_account = self.postvars['to_account'][0]
        amount = float(self.postvars['amount'][0])
        transfer_type = self.postvars['transfer_type'][0]
        if transfer_type == 'balance':
            if amount > g.balance:
                reason = "balance not enough"
                xlog.warn("transfer fail:%s", reason)
                return self.response_json({"res": "fail", "reason": reason})
            end_time = 0
        elif transfer_type == "quota":
            end_time = int(self.postvars['end_time'][0])
        else:
            reason = "transfer type not support:%s" % transfer_type
            xlog.warn("transfer fail:%s", reason)
            return self.response_json({"res": "fail", "reason": reason})

        req_info = {
            "account": g.config.login_account,
            "password": g.config.login_password,
            "transfer_type": transfer_type,
            "end_time": end_time,
            "to_account": to_account,
            "amount": amount
        }

        res, info = proxy_session.call_api("/transfer", req_info)
        if not res:
            xlog.warn("transfer fail:%s", info)
            return self.response_json({
                "res": "fail",
                "reason": info
            })

        self.response_json({"res": "success"})

    def req_get_history_handler(self):
        req = urlparse.urlparse(self.path).query
        reqs = urlparse.parse_qs(req, keep_blank_values=True)

        req_info = {
            "account": g.config.login_account,
            "password": g.config.login_password,
            "start": int(reqs['start'][0]),
            "end": int(reqs['end'][0]),
            "limit": int(reqs['limit'][0])
        }

        res, info = proxy_session.call_api("/get_history", req_info)
        if not res:
            xlog.warn("get history fail:%s", info)
            return self.response_json({
                "res": "fail",
                "reason": info
            })
        self.response_json({
            "res": "success",
            "history": info["history"]
        })

    def req_status(self):
        def convert(num, units=('B', 'KB', 'MB', 'GB')):
            for unit in units:
                if num >= 1024:
                    num /= 1024.0
                else:
                    break
            return '{:.1f} {}'.format(num, unit)

        res = {}
        rtts = []
        recent_sent = 0
        recent_received = 0
        total_sent = 0
        total_received = 0
        for front in all_fronts:
            name = front.name
            score = front.get_score()
            if score is None:
                score = "False"
            else:
                score = int(score)
            rtts.append(front.get_rtt())
            recent_sent += front.recent_sent
            recent_received += front.recent_received
            total_sent += front.total_sent
            total_received += front.total_received
            res[name] = {
                "score": score,
                "success_num": front.success_num,
                "fail_num": front.fail_num,
                "worker_num": front.worker_num(),
                "total_traffics": "Up: %s / Down: %s" % (convert(front.total_sent), convert(front.total_received))
            }

        res["global"] = {
            "socks_addr": "SOCKS5://%s:%d" % (g.config.socks_host, g.config.socks_port),
            "handle_num": g.socks5_server.handler.handle_num,
            "rtt": int(max(rtts)) or 9999,
            "roundtrip_num": g.stat["roundtrip_num"],
            "slow_roundtrip": g.stat["slow_roundtrip"],
            "timeout_roundtrip": g.stat["timeout_roundtrip"],
            "resend": g.stat["resend"],
            "speed": "Up: %s/s / Down: %s/s" % (convert(recent_sent / 5.0), convert(recent_received / 5.0)),
            "total_traffics": "Up: %s / Down: %s" % (convert(total_sent), convert(total_received))
        }

        self.response_json({
            "res": "success",
            "status": res

        })
