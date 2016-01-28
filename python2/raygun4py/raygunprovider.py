import sys
import copy
import socket
import logging
import jsonpickle
import requests
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings()

from raygun4py import raygunmsgs
from raygun4py import utilities

log = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    'before_send_callback': None,
    'grouping_key_callback': None,
    'filtered_keys': [],
    'ignored_exceptions': [],
    'proxy': None,
    'transmit_global_variables': True,
    'transmit_local_variables': True,
    'userversion': "Not defined",
    'user': None,
    'http_timeout': 10.0
}


class RaygunSender:

    api_key = None
    endpointprotocol = 'https://'
    endpointhost = 'api.raygun.io'
    endpointpath = '/entries'

    def __init__(self, api_key, config=None):
        if (api_key):
            self.api_key = api_key
        else:
            log.warning("RaygunProvider error: ApiKey not set, errors will not be transmitted")

        try:
            import ssl
        except ImportError:
            log.warning("RaygunProvider error: No SSL support available, cannot send. Please"
                        "compile the socket module with SSL support.")

        # Set up the default values
        default_config = copy.deepcopy(DEFAULT_CONFIG)
        default_config.update(config or {})
        for k, v in default_config.items():
            setattr(self, k, v)

        #for k, v in default_config.items():
        #    print k + " - " + str(v)

    def set_version(self, version):
        if isinstance(version, basestring):
            self.userversion = version

    def set_user(self, user):
        self.user = user

    def ignore_exceptions(self, exceptions):
        if isinstance(exceptions, list):
            self.ignored_exceptions = exceptions

    def filter_keys(self, keys):
        if isinstance(keys, list):
            self.filtered_keys = keys

    def set_proxy(self, host, port):
        self.proxy = {
            'host': host,
            'port': port
        }

    def on_before_send(self, callback):
        if callable(callback):
            self.before_send_callback = callback

    def on_grouping_key(self, callback):
        if callable(callback):
            self.grouping_key_callback = callback

    def send_exception(self, exception=None, exc_info=None, **kwargs):
        options = {
            'transmitLocalVariables': self.transmit_local_variables,
            'transmitGlobalVariables': self.transmit_global_variables
        }

        if exc_info is None:
            exc_info = sys.exc_info()

        exc_type, exc_value, exc_traceback = exc_info

        if exception is not None:
            errorMessage = raygunmsgs.RaygunErrorMessage(type(exception), exception, exc_traceback, options)
        else:
            errorMessage = raygunmsgs.RaygunErrorMessage(exc_type, exc_value, exc_traceback, options)

        try:
            del exc_type, exc_value, exc_traceback
        except Exception as e:
            raise

        tags, customData, httpRequest = self._parse_args(kwargs)
        message = self._create_message(errorMessage, tags, customData, httpRequest)
        message = self._transform_message(message)

        if message is not None:
            return self._post(message)

    def _parse_args(self, kwargs):
        tags = kwargs['tags'] if 'tags' in kwargs else None
        customData = kwargs['userCustomData'] if 'userCustomData' in kwargs else None

        httpRequest = None
        if 'httpRequest' in kwargs:
            httpRequest = kwargs['httpRequest']
        elif 'request' in kwargs:
            httpRequest = kwargs['request']

        return tags, customData, httpRequest

    def _create_message(self, raygunExceptionMessage, tags, userCustomData, httpRequest):
        return raygunmsgs.RaygunMessageBuilder().new() \
            .set_machine_name(socket.gethostname()) \
            .set_version(self.userversion) \
            .set_client_details() \
            .set_exception_details(raygunExceptionMessage) \
            .set_environment_details() \
            .set_tags(tags) \
            .set_customdata(userCustomData) \
            .set_request_details(httpRequest) \
            .set_user(self.user) \
            .build()

    def _transform_message(self, message):
        message = utilities.ignore_exceptions(self.ignored_exceptions, message)

        if message is not None:
            message = utilities.filter_keys(self.filtered_keys, message)
            message['details']['groupingKey'] = utilities.execute_grouping_key(self.grouping_key_callback, message)

        if self.before_send_callback is not None:
            mutated_payload = self.before_send_callback(message['details'])

            if mutated_payload is not None:
                message['details'] = mutated_payload
            else:
                return None

        return message

    def _post(self, raygunMessage):
        json = jsonpickle.encode(raygunMessage, unpicklable=False)

        try:
            headers = {
                "X-ApiKey": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "raygun4py"
            }

            response = requests.post(self.endpointprotocol + self.endpointhost + self.endpointpath,
                                     headers=headers, data=json, timeout=self.http_timeout)
        except Exception as e:
            log.error(e)
            return 400, "Exception: Could not send"
        return response.status_code, response.text


class RaygunHandler(logging.Handler):
    def __init__(self, api_key, version=None):
        logging.Handler.__init__(self)
        self.sender = RaygunSender(api_key)
        self.version = version

    def emit(self, record):
        userCustomData = {
            "Logger Message": record.msg
        }
        self.sender.send_exception(userCustomData=userCustomData)
