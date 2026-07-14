from typing import Any

class APIException(Exception):
    def __init__(self, status_code:int,msg:str,data:Any = None):
        self.status_code = status_code
        self.msg = msg
        self.data = data