# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""DICOMWeb API client."""

import abc
import http.client as http_client
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple
import uuid

from hcls_imaging_ml_toolkit import dicom_json
from hcls_imaging_ml_toolkit import dicom_path
from hcls_imaging_ml_toolkit import tags
import google.auth
import google.auth.credentials
import google_auth_httplib2
import httplib2
from requests_toolbelt.multipart import decoder
import retrying
import urllib3

CLOUD_HEALTHCARE_API_URL = 'https://healthcare.googleapis.com/v1'

_TOO_MANY_REQUESTS_ERROR = 429


def _IsRetriableHTTPError(ret_value: Tuple[httplib2.Response, str]) -> bool:
  """Determines whether the given HTTP exception is retriable.

  Args:
    ret_value: The return Tuple returned from the request method.

  Returns:
    Whether the exception can be retried.
  """
  retriable_http_errors = (
      _TOO_MANY_REQUESTS_ERROR,
      http_client.REQUEST_TIMEOUT,
      http_client.SERVICE_UNAVAILABLE,
      http_client.GATEWAY_TIMEOUT,
  )

  resp, _ = ret_value
  return resp.status in retriable_http_errors


def PathToUrl(path: dicom_path.Path) -> str:
  """Constructs full URL from a DICOMweb path using the CHC API prefix.

  Args:
    path: DICOMweb Path object.

  Returns:
    Full URL in the form of 'https://.../dicomStores/...'
  """
  return PathStrToUrl(str(path))


def PathStrToUrl(path_str: str) -> str:
  """Constructs full URL from a DICOMweb path or query using the CHC API prefix.

  Args:
    path_str: Path string for a DICOM resource or a DICOMweb query string in the
      form of 'projects/<project_id>/.../dicomStores/...'

  Returns:
    Full URL in the form of 'https://.../dicomStores/...'
  """
  return dicom_path.DicomPathJoin(CLOUD_HEALTHCARE_API_URL, path_str)


# DicomBulkData represents bulk data that is encoded as part of STOW JSON.
# The original class was moved to `dicom_bulk_data.py`. This definition is for
# backward compatibility only, and may be removed in future updates.
DicomBulkData = dicom_json.DicomBulkData


class UnexpectedResponseError(Exception):
  """Exception describing an unexpected response to a DicomWeb query."""

  pass


class DicomWebClient(metaclass=abc.ABCMeta):
  """Abstract base class for DICOMWeb client."""

  @abc.abstractmethod
  def QidoRs(
      self, qido_url: str, timeout: Optional[int] = 3600
  ) -> List[Dict[str, Any]]:
    """Performs a QidoRs request and returns the parsed JSON response.

    Args:
      qido_url: URL for the QIDO request.
      timeout: Http timeout in seconds.

    Returns:
      The parsed JSON response content or empty list if no contents are found.
    """
    raise NotImplementedError

  @abc.abstractmethod
  def WadoRs(
      self,
      wado_url: str,
      accept_header: Optional[str] = None,
      timeout: Optional[int] = 3600,
  ) -> bytes:
    """Performs a WadoRs request and returns the response.

    Args:
      wado_url: URL for the WADO request.
      accept_header: Value of the Accept header to use. If set to None, no
        Accept header will be used.
      timeout: Http timeout in seconds.

    Returns:
      The content of the first (and only) part as a string.
    """
    raise NotImplementedError

  @abc.abstractmethod
  def StowRs(
      self,
      stow_url: str,
      dcmbytes_list: List[bytes],
      timeout: Optional[int] = 3600,
  ) -> None:
    """Performs a StowRs request.

    Args:
      stow_url: URL for the STOW request.
      dcmbytes_list: List of serialized DICOM instances to store.
      timeout: Http timeout in seconds.

    Returns:
      None
    """
    raise NotImplementedError

  @abc.abstractmethod
  def StowRsJson(
      self,
      stow_url: str,
      dicom_dict_list: List[Dict[str, Any]],
      bulkdata_list: Iterable[dicom_json.DicomBulkData],
      timeout: Optional[int] = 3600,
  ) -> None:
    """Performs a StowRs JSON request.

    Args:
      stow_url: URL for the STOW request.
      dicom_dict_list: List of dictionaries, each dictionary containing DICOM
        metadata corresponding to a DICOM instance to store. Gets encoded into
        DICOM JSON.
      bulkdata_list: DICOM bulkdata to store. Each instance corresponds to a
        reference from DICOM metadata in dicom_dict_list.
      timeout: Http timeout in seconds.

    Returns:
      None
    """
    raise NotImplementedError

  @abc.abstractmethod
  def DeleteRs(self, delete_url: str, timeout: Optional[int] = 3600) -> None:
    """Performs a DeleteRs request.

    Args:
      delete_url: URL for the DELETE request. Can be instance, series, study.
      timeout: Http timeout in seconds.

    Returns:
      None
    """
    raise NotImplementedError


class DicomWebClientImpl(DicomWebClient):
  """Concrete implementation, using REST HTTP calls."""

  @classmethod
  def _NotHttp2xx(cls, status: int):
    return status // 100 != 2

  def __init__(
      self, credentials: Optional[google.auth.credentials.Credentials] = None
  ):
    super(DicomWebClientImpl, self).__init__()
    self._credentials = credentials
    # Support integration testing through dependency injection.
    if self._credentials is None:
      credentials, _ = google.auth.default()
      self._credentials = google.auth.credentials.with_scopes_if_required(
          credentials, ['https://www.googleapis.com/auth/cloud-platform']
      )

  @retrying.retry(
      retry_on_result=_IsRetriableHTTPError,
      wait_exponential_multiplier=2000,
      wait_exponential_max=32000,
      stop_max_attempt_number=5,
  )
  def _InvokeHttpRequest(
      self,
      uri: str,
      method: str,
      timeout: Optional[int] = 3600,
      body: Optional[str] = None,
      headers: Optional[Dict[str, Any]] = None,
  ) -> Tuple[httplib2.Response, str]:
    """Invokes a Http request to DICOMWeb API client.

    Args:
      uri: URI of Http request.
      method: Http method type e.g. 'GET'
      timeout: Http timeout in seconds.
      body: Http request body.
      headers: Http request headers.

    Returns:
      Tuple of httplib2.Response and string content.
    """
    http = google_auth_httplib2.AuthorizedHttp(self._credentials)
    http.force_exception_to_status_code = True
    http.timeout = timeout
    return http.request(uri, method, body, headers)

  def QidoRs(
      self, qido_url: str, timeout: Optional[int] = 3600
  ) -> List[Dict[str, Any]]:
    """Performs the request, and returns the parsed JSON response.

    Args:
      qido_url: URL for the QIDO request.
      timeout: Http timeout in seconds.

    Returns:
      The parsed JSON response content or empty list if no contents are found.

    Raises:
      UnexpectedResponseError: If the response status was not success.
    """
    resp, content = self._InvokeHttpRequest(qido_url, 'GET', timeout=timeout)
    if DicomWebClientImpl._NotHttp2xx(resp.status):
      raise UnexpectedResponseError(
          'QidoRs error. Response Status: %d,\nURL: %s,\nContent: %s.'
          % (resp.status, qido_url, content)
      )
    if resp.status == 204:  # Empty query
      return []
    return json.loads(content)

  def WadoRs(
      self,
      wado_url: str,
      accept_header: Optional[str] = None,
      timeout: Optional[int] = 3600,
  ) -> bytes:
    """Performs the request, parses the multipart response, and returns content.

    Args:
      wado_url: URL for the WADO request.
      accept_header: Value of the Accept header to use. If set to None, no
        Accept header will be used.
      timeout: Http timeout in seconds.

    Returns:
      The content of the first (and only) part as a string.

    Raises:
      UnexpectedResponseError: If the response status was not success or the
        number of parts in the multipart response is different from 1.
    """
    resp, content = self._InvokeHttpRequest(
        wado_url,
        'GET',
        timeout=timeout,
        headers={'Accept': accept_header} if accept_header is not None else {},
    )
    if DicomWebClientImpl._NotHttp2xx(resp.status):
      raise UnexpectedResponseError(
          'WadoRs error. Response Status: %d,\nURL: %s,\nContent: %s.'
          % (resp.status, wado_url, content)
      )
    multipart_data = decoder.MultipartDecoder(content, resp['content-type'])
    num_parts = len(multipart_data.parts)
    if num_parts != 1:
      raise UnexpectedResponseError(
          'WadoRs multipart response expected to have a single part.'
          ' Actual: %d.\nURL: %s' % (num_parts, wado_url)
      )
    return multipart_data.parts[0].content

  def StowRs(
      self,
      stow_url: str,
      dcmbytes_list: List[bytes],
      timeout: Optional[int] = 3600,
  ) -> None:
    """Stores the serialized instance via StowRs.

    Args:
      stow_url: URL for the STOW request.
      dcmbytes_list: List of serialized DICOM instances to store.
      timeout: Http timeout in seconds.

    Returns:
      None

    Raises:
      UnexpectedResponseError: If StowRs response status was not successful.
    """
    application_type = 'dicom'
    parts = []
    for dcmbytes in dcmbytes_list:
      part = urllib3.fields.RequestField(
          name='placeholder',
          data=dcmbytes,
          headers={'Content-Type': 'application/%s' % application_type},
      )
      parts.append(part)
    return self._StowRs(stow_url, application_type, parts, timeout=timeout)

  def StowRsJson(
      self,
      stow_url: str,
      dicom_dict_list: List[Dict[str, Any]],
      bulkdata_list: Iterable[dicom_json.DicomBulkData],
      timeout: Optional[int] = 3600,
  ) -> None:
    """Stores the instance(s) via StowRs JSON.

    Args:
      stow_url: URL for the STOW request.
      dicom_dict_list: List of dictionaries, each dictionary containing DICOM
        metatata corresponding to a DICOM instance to store. Gets encoded into
        DICOM JSON.
      bulkdata_list: DICOM bulkdata to store. Each instance corresponds to a
        reference from DICOM metadata in dicom_dict_list.
      timeout: Http timeout in seconds.

    Returns:
      None

    Raises:
      ValueError if the content type is invalid.
    """
    # Write the JSON part.
    parts = []
    application_type = 'dicom+json'
    jsonstr = json.dumps(dicom_dict_list)
    part = urllib3.fields.RequestField(
        name='placeholder',
        data=jsonstr,
        headers={'Content-Type': 'application/%s' % application_type},
    )
    parts.append(part)

    # Write the bulkdata part(s).
    for bulkdata in bulkdata_list:
      type_split = bulkdata.content_type.split('/')
      if len(type_split) != 2:
        raise ValueError('MIME type must be in form "type/sub-type"')
      part = urllib3.fields.RequestField(
          name=bulkdata.uri,
          data=bulkdata.data,
          headers={
              'Content-Location': bulkdata.uri,
              'Content-Type': bulkdata.content_type,
          },
      )
      parts.append(part)

    self._StowRs(stow_url, application_type, parts, timeout=timeout)

  def _StowRs(
      self,
      stow_url: str,
      application_type: str,
      parts: List[urllib3.fields.RequestField],
      timeout: Optional[int] = 3600,
  ) -> None:
    """Stores the instance(s) via StowRs.

    Args:
      stow_url: URL for the STOW request.
      application_type: MIME appliction type.
      parts: List of RequestField's containing HTTP multipart data.
      timeout: Http timeout in seconds.

    Raises:
      UnexpectedResponseError: If StowRs response status was not success.
    """
    # Use a random boundary string.
    boundary = str(uuid.uuid4())
    content_type = (
        'multipart/related; type="application/%s"; boundary="%s"'
    ) % (application_type, boundary)
    headers = {'content-type': content_type}
    # To be noted that this is intended for multipart/form-data, however the
    # structure of the message is the same as what is used for mutlipart/related
    # and it works fine in our use-case.
    body, _ = urllib3.filepost.encode_multipart_formdata(parts, boundary)
    resp, content = self._InvokeHttpRequest(
        stow_url, method='POST', timeout=timeout, body=body, headers=headers
    )

    if DicomWebClientImpl._NotHttp2xx(resp.status):
      raise UnexpectedResponseError(
          'StowRs error. Response Status: %d,\nURL: %s,\nContent: %s.'
          % (resp.status, stow_url, content)
      )

  def DeleteRs(self, delete_url: str, timeout: Optional[int] = 3600) -> None:
    """Performs delete request on the specified URL.

    Args:
      delete_url: URL for the DELETE request. Can be instance, series, study.
      timeout: Http timeout in seconds.

    Returns:
      None

    Raises:
      UnexpectedResponseError: If DeleteRs response status was not success.
    """
    resp, content = self._InvokeHttpRequest(
        delete_url, 'DELETE', timeout=timeout
    )
    if DicomWebClientImpl._NotHttp2xx(resp.status):
      raise UnexpectedResponseError(
          'DeleteRs error. Response Status: %d,\nURL: %s,\nContent: %s.'
          % (resp.status, delete_url, content)
      )


def GetStudyMetadata(
    dwc: DicomWebClient, dicomweb_url: str, study_uid: str
) -> Dict[str, Any]:
  """Fetches Qido study level tags and returns the response.

  Args:
    dwc: DICOMWeb client to retrieve DICOM instances.
    dicomweb_url: URL of the DICOMweb API for the DICOM store containing the CT
      scan, i.e.  https://.../dicomStores/<dicom_store_name>/dicomWeb.
    study_uid: UID of the scan's study.

  Returns:
    Study response which is a Dict representing Dicom Json.
  """
  qido_study_url = '%s/studies?StudyInstanceUID=%s&includefield=all' % (
      dicomweb_url,
      study_uid,
  )
  resp = dwc.QidoRs(qido_study_url)
  return resp[0]


def GetInstancesMetadata(
    dwc: DicomWebClient,
    dicomweb_url: str,
    study_uid: str,
    tag_list: List[tags.DicomTag],
    limit: int,
) -> List[Dict[str, Dict[str, Any]]]:
  """Fetches the specified tags for all instances in the given study.

  Args:
    dwc: DICOMWeb client to retrieve DICOM instance metadata.
    dicomweb_url: URL of the DICOMweb API for the DICOM store containing the
      study of interest.
    study_uid: Study UID of the study to retrieve the instance-level data.
    tag_list: The DICOM tags to retrieve for each found instance.
    limit: The limit for the number instances to query.

  Returns:
    series_dict: List of Dictionaries containing tags/values of individual
      instances.
  """

  qido_study_url = dicom_path.DicomPathJoin(
      dicomweb_url, 'studies', study_uid, 'instances'
  )
  suffix = '&'.join('includefield=%s' % tag.number for tag in tag_list)
  suffix += '&limit=%s' % (limit)
  qido_study_url = '%s/?%s' % (qido_study_url, suffix)
  query_response = dwc.QidoRs(qido_study_url)
  return query_response
