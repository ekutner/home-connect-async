""" Common classes shared across the code """

class HomeConnectError(Exception):
    """ Common exception class for the SDK """
    def __init__(self, msg:str = None, code:int = None, response = None, inner_exception = None):
        self.msg = msg
        self.code = code
        self.response = response
        self.inner_exception = inner_exception
        if response:
            self.error_key = response.error_key
            self.error_description = response.error_description
            if not code: self.code = response.status
        else:
            self.error_key = None
            self.error_description = None

        super().__init__(msg, code, self.error_key, self.error_description, inner_exception)

