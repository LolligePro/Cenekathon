"""Small HTTP client for the shared read-only database. Uses the standard library."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request,urlopen


class RemoteDatabase:
    def __init__(self,url=None,timeout=30):
        self.url=(url or os.environ.get('SKILLMARKET_API_URL','http://127.0.0.1:8765')).rstrip('/')
        self.timeout=timeout

    def _request(self,path,body=None):
        request=Request(self.url+path,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                error=json.load(exc).get('error',str(exc))
            finally:
                exc.close()
            raise ValueError(error) from exc

    def health(self):
        return self._request('/health')

    def schema(self):
        return self._request('/schema')

    def query(self,sql,parameters=None,max_rows=1000):
        return self._request('/query',{'sql':sql,'parameters':parameters or [],'max_rows':max_rows})

    def dataframe(self,sql,parameters=None,max_rows=1000):
        import pandas as pd
        result=self.query(sql,parameters,max_rows)
        frame=pd.DataFrame(result['rows'],columns=[c['name'] for c in result['columns']])
        frame.attrs['truncated']=result['truncated']
        return frame
