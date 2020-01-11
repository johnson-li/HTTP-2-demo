from __future__ import print_function

from twisted.internet import reactor
from twisted.internet.endpoints import connectProtocol, SSL4ClientEndpoint
from twisted.internet.protocol import Protocol
from twisted.internet.ssl import optionsForClientTLS, PrivateCertificate, Certificate
from twisted.internet import defer, error
from twisted.python.modules import getModule
from hyperframe.frame import SettingsFrame
from h2.connection import H2Connection
from h2.events import (
    ResponseReceived, DataReceived, StreamEnded,
    StreamReset, SettingsAcknowledged,
)


AUTHORITY = u'example.localhost'
PATH = '/'
SIZE = 4096


class H2Protocol(Protocol):
    def __init__(self):
        self.conn = H2Connection()
        self.known_proto = None
        self.request_made = False

    def connectionMade(self):
        self.conn.initiate_connection()

        # This reproduces the error in #396, by changing the header table size.
        self.conn.update_settings({SettingsFrame.HEADER_TABLE_SIZE: SIZE})

        self.transport.write(self.conn.data_to_send())

    def dataReceived(self, data):
        if not self.known_proto:
            self.known_proto = self.transport.negotiatedProtocol
            assert self.known_proto == b'h2'

        events = self.conn.receive_data(data)

        for event in events:
            if isinstance(event, ResponseReceived):
                self.handleResponse(event.headers, event.stream_id)
            elif isinstance(event, DataReceived):
                self.handleData(event.data, event.stream_id)
            elif isinstance(event, StreamEnded):
                self.endStream(event.stream_id)
            elif isinstance(event, SettingsAcknowledged):
                self.settingsAcked(event)
            elif isinstance(event, StreamReset):
                reactor.stop()
                raise RuntimeError("Stream reset: %d" % event.error_code)
            else:
                print(event)

        data = self.conn.data_to_send()
        if data:
            self.transport.write(data)

    def settingsAcked(self, event):
        # Having received the remote settings change, lets send our request.
        if not self.request_made:
            self.sendRequest()

    def handleResponse(self, response_headers, stream_id):
        for name, value in response_headers:
            print("%s: %s" % (name.decode('utf-8'), value.decode('utf-8')))

        print("")

    def handleData(self, data, stream_id):
        print(data, end='')

    def endStream(self, stream_id):
        self.conn.close_connection()
        self.transport.write(self.conn.data_to_send())
        self.transport.loseConnection()
        reactor.stop()

    def sendRequest(self):
        request_headers = [
            (':method', 'GET'),
            (':authority', AUTHORITY),
            (':scheme', 'https'),
            (':path', PATH),
            ('user-agent', 'hyper-h2/1.0.0'),
        ]
        self.conn.send_headers(1, request_headers, end_stream=True)
        self.request_made = True

certData = open('cert/example.crt').read()
certificate = Certificate.loadPEM(certData)

options = optionsForClientTLS(
    hostname=AUTHORITY,
    acceptableProtocols=[b'h2'],
    trustRoot=certificate,
)

class ShowCertificate(Protocol):
        def connectionMade(self):
            self.transport.write(b"GET / HTTP/1.0\r\n\r\n")
            self.done = defer.Deferred()
        def dataReceived(self, data):
            certificate = Certificate(self.transport.getPeerCertificate())
            print("OK:", certificate)
            self.transport.abortConnection()
        def connectionLost(self, reason):
            print("Lost.")
            if not reason.check(error.ConnectionClosed):
                print("BAD:", reason.value)
            self.done.callback(None)


connectProtocol(
    SSL4ClientEndpoint(reactor, AUTHORITY, 8445, options),
    #ShowCertificate(),
    H2Protocol(),
)
reactor.run()

