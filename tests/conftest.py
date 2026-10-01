import pytest
from fastapi.testclient import TestClient

from oss_lab.core.api import create_app
from oss_lab.core.stack import Stack

ORD = "/tmf-api/serviceOrdering/v4/serviceOrder"
SVC = "/tmf-api/serviceInventory/v4/service"


def l3(device="r1", iface="ethernet-1/1", ip="10.0.0.1", **extra):
    chars = {"device": device, "interface": iface, "ipv4": ip, **extra}
    return {
        "serviceSpecification": {"id": "l3-interface"},
        "serviceCharacteristic": [{"name": k, "value": v} for k, v in chars.items()],
    }


@pytest.fixture
def stack():
    return Stack()


@pytest.fixture
def client(stack):
    return TestClient(create_app(stack))


@pytest.fixture
def order(client):
    def _order(*items, order_id=None):
        body = {"orderItem": list(items)}
        if order_id:
            body["id"] = order_id
        r = client.post(ORD, json=body)
        assert r.status_code == 201, r.text
        return r.json()

    return _order
