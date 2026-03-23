"""SAP Business One Service Layer client."""

import os
import time
import urllib3
from dotenv import load_dotenv
import requests

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ODATA_JUNK = {"odata.metadata", "odata.etag", "odata.type"}


class SAPClient:
    """Synchronous SAP B1 Service Layer client with auto-login and retry."""

    def __init__(self):
        self.base_url = os.environ["SAP_SL_URL"]
        self.company_db = os.environ["SAP_COMPANY_DB"]
        self.username = os.environ["SAP_USERNAME"]
        self.password = os.environ["SAP_PASSWORD"]
        self.verify_ssl = os.environ.get("SAP_VERIFY_SSL", "false").lower() == "true"
        self.bpl_id = int(os.environ.get("SAP_BPL_ID", "3"))
        self.session = requests.Session()
        self.session.verify = self.verify_ssl
        self.session.headers.update({"Content-Type": "application/json"})
        self._logged_in = False

    def login(self):
        resp = self.session.post(f"{self.base_url}/Login", json={
            "CompanyDB": self.company_db,
            "UserName": self.username,
            "Password": self.password,
        })
        resp.raise_for_status()
        self._logged_in = True
        return resp.json()

    def logout(self):
        if self._logged_in:
            try:
                self.session.post(f"{self.base_url}/Logout")
            except Exception:
                pass
            self._logged_in = False

    def _ensure_session(self):
        if not self._logged_in:
            self.login()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        self._ensure_session()
        url = f"{self.base_url}{path}"
        # SAP SL requires literal $ in OData params — don't let requests encode them
        req = requests.Request(method, url, **kwargs)
        prepared = self.session.prepare_request(req)
        # Restore literal $ signs that requests URL-encoded to %24
        if prepared.url and "%24" in prepared.url:
            prepared.url = prepared.url.replace("%24", "$")
        resp = self.session.send(prepared)
        if resp.status_code == 401:
            self._logged_in = False
            self.login()
            prepared = self.session.prepare_request(req)
            if prepared.url and "%24" in prepared.url:
                prepared.url = prepared.url.replace("%24", "$")
            resp = self.session.send(prepared)
        resp.raise_for_status()
        return resp

    def get(self, path: str, params: dict | None = None) -> dict | list:
        resp = self._request("GET", path, params=params)
        if resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {"_raw": resp.text}

    def get_all(self, path: str, page_size: int = 100, delay: float = 0.2) -> list:
        """Fetch all records with pagination."""
        results = []
        skip = 0
        sep = "&" if "?" in path else "?"
        while True:
            data = self.get(f"{path}{sep}$top={page_size}&$skip={skip}")
            items = data.get("value", [])
            if not items:
                break
            results.extend(items)
            if "odata.nextLink" not in data:
                break
            skip += page_size
            time.sleep(delay)
        return results

    def post(self, path: str, payload: dict) -> dict:
        resp = self._request("POST", path, json=payload)
        if resp.status_code == 204:
            return {}
        return resp.json()

    def patch(self, path: str, payload: dict, replace_collections: bool = False) -> None:
        headers = {}
        if replace_collections:
            headers["B1S-ReplaceCollectionsOnPatch"] = "true"
        resp = self._request("PATCH", path, json=payload, headers=headers)
        # PATCH returns 204 No Content — nothing to parse

    def delete(self, path: str) -> None:
        self._request("DELETE", path)

    @staticmethod
    def clean_odata(data: dict) -> dict:
        """Remove OData metadata fields before using data in PATCH."""
        return {k: v for k, v in data.items() if k not in ODATA_JUNK}

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()
