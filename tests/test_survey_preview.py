"""
The admin survey preview (org.deployment_preview).

Adapted from trainiq. An admin walks the survey exactly as a respondent sees
it, and NOTHING is recorded: no ResponseSession, no SurveyResponse, no change
to responses_received. Preview answers must never reach the collected
responses or any report.
"""
import pytest

from app import db
from app.models import ResponseSession, SurveyDeployment, SurveyResponse
from app.provisioning import clone_instrument_to_org
from tests.conftest import login
from tests.factories import (
    make_cycle, make_deployment, make_master_instrument, make_org,
    make_org_admin,
)


@pytest.fixture
def world(db):
    make_master_instrument()
    org = make_org('Kilimo Dairies')
    clone_instrument_to_org(org.id)
    cycle = make_cycle(org, 'KD-PREVIEW', status='open')
    deployment = make_deployment(org, cycle, public_slug='slug-preview-aaa')
    other = make_org('Globex Inc')
    data = {
        'deployment_id': deployment.id,
        'admin': make_org_admin(org, email='l2@kilimo.test'),
        'other_admin': make_org_admin(other, email='l2@globex.test'),
    }
    db.session.commit()
    return data


def _url(world, index=None):
    base = f'/org/deployments/{world["deployment_id"]}/preview'
    return base if index is None else f'{base}/{index}'


def test_walking_the_whole_preview_records_nothing(client, world):
    login(client, world['admin'])

    res = client.get(_url(world))
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'Preview' in body and "won't be saved" in body
    assert 'name="direction"' not in body  # nav is links, not submit buttons

    index = 1
    while True:
        res = client.get(_url(world, index))
        assert res.status_code == 200
        if 'Preview complete' in res.get_data(as_text=True):
            break
        index += 1
        assert index < 20, 'preview never reached its end page'
    assert index == 5  # four domain sections, then the end page

    # Even a hand-crafted POST must not write.
    assert client.post(_url(world, 1), data={'direction': 'next'}).status_code == 405

    assert db.session.query(ResponseSession).count() == 0
    assert db.session.query(SurveyResponse).count() == 0
    deployment = db.session.get(SurveyDeployment, world['deployment_id'])
    db.session.refresh(deployment)
    assert deployment.responses_received == 0


def test_the_preview_404s_another_tenants_deployment(client, world):
    login(client, world['other_admin'])
    assert client.get(_url(world)).status_code == 404


def test_the_preview_requires_login(client, world):
    res = client.get(_url(world))
    assert res.status_code in (302, 401)


def test_out_of_range_sections_404(client, world):
    login(client, world['admin'])
    assert client.get(_url(world, 0)).status_code == 404
    assert client.get(_url(world, 99)).status_code == 404
