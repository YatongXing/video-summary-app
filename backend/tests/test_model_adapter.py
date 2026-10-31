"""Independent provider-neutral evidence and offline error checks."""
from dataclasses import fields, replace
import io
import socket
import pytest
from app.model_adapter import AudioEvidence, VisualEvidence, ModelRequest, ModelResponse, Observation, TokenUsage, AdapterError, FakeModelAdapter, validate_response

def request(audio=True):
    return ModelRequest(3,(VisualEvidence(0,b'jpeg-first'),VisualEvidence(2,b'jpeg-last')),
        (AudioEvidence(.5,3,lambda:io.BytesIO(b'synthetic audio bytes')), ) if audio else (),('sampled frames',))

def test_fake_consumes_both_modalities_without_network(monkeypatch):
    def forbidden(*a,**k): raise AssertionError('Network access is forbidden')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    response=FakeModelAdapter().analyze(request())
    assert {item.modality for item in response.observations}=={'audio','visual'}
    assert next(item.timestamp_seconds for item in response.observations if item.modality=='audio')==.5
    assert response==FakeModelAdapter().analyze(request())
    assert 'OFFLINE TEST' in response.candidate['summary']
    assert response.model_version and response.adapter_version and response.request_id
    assert response.usage==TokenUsage() and response.usage.estimated_cost is None

def test_visual_only_is_explicitly_supported():
    response=FakeModelAdapter().analyze(request(False))
    assert {item.modality for item in response.observations}=={'visual'}

def test_request_contract_excludes_private_identity_and_paths():
    names={field.name for field in fields(ModelRequest)}
    assert not names.intersection({'task_id','user_id','email','filename','url','path','api_key','credentials'})
    value=request()
    assert b'jpeg-first'.decode() not in repr(value)
    assert 'synthetic audio bytes' not in repr(value)

@pytest.mark.parametrize('kind,category', [('timeout','EXTERNAL_TIMEOUT'),('rate_limit','EXTERNAL_RATE_LIMIT'),('unavailable','EXTERNAL_UNAVAILABLE'),('input','USER_INPUT'),('invalid_response','INTERNAL'),('unrecognized','INTERNAL')])
def test_errors_classified_without_fake_usage_or_cost(kind,category):
    with pytest.raises(AdapterError) as error: FakeModelAdapter(kind).analyze(request())
    assert error.value.failure_category==category
    assert error.value.usage.estimated_cost is None and error.value.usage.input_tokens is None
    assert error.value.request_id.startswith('fake-')

def test_cancel_and_invalid_timestamps_rejected():
    with pytest.raises(AdapterError) as error: FakeModelAdapter().analyze(request(),lambda:False)
    assert error.value.kind=='cancelled'
    with pytest.raises(AdapterError): FakeModelAdapter().analyze(replace(request(),frames=(VisualEvidence(4,b'x'),)))
    with pytest.raises(AdapterError): FakeModelAdapter().analyze(replace(request(),language='fr'))

@pytest.mark.parametrize('mutation', ['time','order','usage','cost'])
def test_invalid_response_is_rejected(mutation):
    response=FakeModelAdapter().analyze(request())
    if mutation=='time': response=replace(response,observations=(Observation('visual',4,'invalid'),))
    if mutation=='order': response=replace(response,candidate={'key_events':[{'timestamp_seconds':2},{'timestamp_seconds':1}]})
    if mutation=='usage': response=replace(response,usage=TokenUsage(input_tokens=-1))
    if mutation=='cost': response=replace(response,usage=TokenUsage(estimated_cost=0.1))
    with pytest.raises(AdapterError): validate_response(response,3)


def test_response_metadata_cannot_contain_raw_url_or_control_text():
    response=FakeModelAdapter().analyze(request())
    for changed in (replace(response,request_id='https://private.invalid/?secret=x'), replace(response,model_version='model\nsecret')):
        with pytest.raises(AdapterError):validate_response(changed,3)
