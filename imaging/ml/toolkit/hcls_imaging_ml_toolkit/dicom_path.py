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
"""Utilities for DICOMweb path manipulation."""
from __future__ import annotations
import dataclasses
import enum
import re
from typing import Match, Optional


class Type(enum.Enum):
  """Type of a resource the path points to."""

  STORE = 'store'
  STUDY = 'study'
  SERIES = 'series'
  INSTANCE = 'instance'


# Used for project ID and location validation
_REGEX_ID_1 = r'[\w-]+'
# Used for dataset ID and dicom store ID validation
_REGEX_ID_2 = r'[\w.-]+'
# Used for DICOM UIDs validation
# '/' is not allowed because the parsing logic in the class uses '/' to
# tokenize the path.
# '@' is not allowed due to security concerns: theoretically it could lead
# to the part before '@' being interpreted as the username, and the part
# after - as the server address, which is a potential vulnerability.
_REGEX_UID = r'[^/@]+'


def DicomPathJoin(*args: str) -> str:
  return '/'.join(args)


@dataclasses.dataclass(frozen=True)
class Path:
  """Represents a path to a DICOM Store or a DICOM resource in CHC API.

  Attributes:
    project_id: Project ID. Must be non-empty.
    location: Location. Must be non-empty.
    dataset_id: Dataset ID. Must be non-empty.
    store_id: DICOM Store ID. Must be non-empty.
    study_uid: DICOM Study UID. Optional.
    series_uid: DICOM Series UID. Optional.
    instance_uid: DICOM Instance UID. Optional.
  """

  project_id: str
  location: str
  dataset_id: str
  store_id: str
  study_uid: Optional[str] = None
  series_uid: Optional[str] = None
  instance_uid: Optional[str] = None

  def __post_init__(self) -> None:
    """Validates path configuration.

    Returns:
      None

    Raises:
      ValueError: Invalid configuration.
    """
    id1_regex = re.compile(_REGEX_ID_1)
    id2_regex = re.compile(_REGEX_ID_2)
    uid_regex = re.compile(_REGEX_UID)
    if id1_regex.fullmatch(self.project_id) is None:
      raise ValueError('Invalid project_id')
    if id1_regex.fullmatch(self.location) is None:
      raise ValueError('Invalid location')
    if id2_regex.fullmatch(self.dataset_id) is None:
      raise ValueError('Invalid dataset_id')
    if id2_regex.fullmatch(self.store_id) is None:
      raise ValueError('Invalid store_id')
    if (
        self.study_uid is not None
        and uid_regex.fullmatch(self.study_uid) is None
    ):
      raise ValueError('Invalid store_id')
    if (
        self.series_uid is not None
        and uid_regex.fullmatch(self.series_uid) is None
    ):
      raise ValueError('Invalid store_id')
    if (
        self.instance_uid is not None
        and uid_regex.fullmatch(self.instance_uid) is None
    ):
      raise ValueError('Invalid store_id')
    self._StudyUidMissing(self.study_uid)
    self._SeriesUidMissing(self.series_uid)

  def _StudyUidMissing(self, value: Optional[str]) -> None:
    if not value:
      if self.series_uid or self.instance_uid:
        raise ValueError(
            'study_uid missing with non-empty series_uid or '
            'instance_uid. series_uid: %s, instance_uid: %s'
            % (self.series_uid, self.instance_uid)
        )

  def _SeriesUidMissing(self, value: Optional[str]) -> None:
    if not value:
      if self.instance_uid:
        raise ValueError(
            'series_uid missing with non-empty instance_uid. instance_uid: %s'
            % self.instance_uid
        )

  def __str__(self):
    """Returns the text representation of the path."""
    store_path_str = DicomPathJoin(
        'projects',
        self.project_id,
        'locations',
        self.location,
        'datasets',
        self.dataset_id,
        'dicomStores',
        self.store_id,
    )
    if self.study_uid is None:
      return store_path_str

    study_path_str = DicomPathJoin(
        store_path_str, 'dicomWeb/studies', self.study_uid
    )
    if self.series_uid is None:
      return study_path_str

    series_path_str = DicomPathJoin(study_path_str, 'series', self.series_uid)
    if self.instance_uid is None:
      return series_path_str

    return DicomPathJoin(series_path_str, 'instances', self.instance_uid)

  @property
  def type(self) -> Type:
    """Type of the DICOM resource corresponding to the path."""
    if not self.study_uid:
      return Type.STORE
    elif not self.series_uid:
      return Type.STUDY
    elif not self.instance_uid:
      return Type.SERIES
    return Type.INSTANCE

  @property
  def dicomweb_path_str(self) -> str:
    """Path to the DICOMweb endpoint for the DICOM Store."""
    return DicomPathJoin(str(self.GetStorePath()), 'dicomWeb')

  def GetStorePath(self) -> 'Path':
    """Returns the sub-path for the DICOM Store within this path."""
    return Path(
        self.project_id,
        self.location,
        self.dataset_id,
        self.store_id,
    )

  def GetStudyPath(self) -> Path:
    """Returns the sub-path for the DICOM Study within this path."""
    if self.type == Type.STORE:
      raise ValueError("Can't get a study path from a store path.")
    return Path(
        self.project_id,
        self.location,
        self.dataset_id,
        self.store_id,
        self.study_uid,
    )

  def GetSeriesPath(self) -> Path:
    """Returns the sub-path for the DICOM Series within this path."""
    if self.type in (Type.STORE, Type.STUDY):
      raise ValueError("Can't get a series path from a %s path." % self.type)
    return Path(
        self.project_id,
        self.location,
        self.dataset_id,
        self.store_id,
        self.study_uid,
        self.series_uid,
    )


def _MatchRegex(regex: str, text_str: str, error_str) -> Match[str]:
  """Matches the regex and returns the match or raises ValueError if failed."""
  match = re.match(regex, text_str)
  if match is None:
    raise ValueError(error_str)
  return match


def _FromString(path_str: str) -> Path:
  """Parses the string and returns the Path object or raises ValueError if failed."""
  store_regex = (
      r'projects/(%s)/locations/(%s)/datasets/(%s)/dicomStores/(%s)'
      r'(.*)' % (_REGEX_ID_1, _REGEX_ID_1, _REGEX_ID_2, _REGEX_ID_2)
  )
  match_err_str = 'Error parsing the path. Path: %s' % path_str
  store_match = _MatchRegex(store_regex, path_str, match_err_str)

  project_id = store_match.group(1)
  location = store_match.group(2)
  dataset_id = store_match.group(3)
  store_id = store_match.group(4)
  store_path_suffix = store_match.group(5)

  if not store_path_suffix or store_path_suffix == '/':
    return Path(project_id, location, dataset_id, store_id, None, None, None)

  studies_regex = r'/dicomWeb/studies/(%s)(.*)' % _REGEX_UID
  studies_match = _MatchRegex(studies_regex, store_path_suffix, match_err_str)
  study_uid = studies_match.group(1)
  study_path_suffix = studies_match.group(2)

  if not study_path_suffix or study_path_suffix == '/':
    return Path(
        project_id, location, dataset_id, store_id, study_uid, None, None
    )

  series_regex = r'/series/(%s)(.*)' % _REGEX_UID
  series_match = _MatchRegex(series_regex, study_path_suffix, match_err_str)
  series_uid = series_match.group(1)
  series_path_suffix = series_match.group(2)

  if not series_path_suffix or series_path_suffix == '/':
    return Path(
        project_id, location, dataset_id, store_id, study_uid, series_uid, None
    )

  instance_regex = r'/instances/(%s)/?$' % _REGEX_UID
  instance_match = _MatchRegex(
      instance_regex, series_path_suffix, match_err_str
  )
  instance_uid = instance_match.group(1)

  return Path(
      project_id,
      location,
      dataset_id,
      store_id,
      study_uid,
      series_uid,
      instance_uid,
  )


def FromString(path_str: str, path_type: Optional[Type] = None) -> Path:
  """Parses the string and returns the Path object or raises ValueError if failed.

  Args:
    path_str: The string containing the path.
    path_type: The expected type of the path or None if no specific type is
      expected.

  Returns:
    The newly constructed Path object.
  Raises:
    ValueError if the path cannot be parsed or the actual path type doesn't
      match the specified expected type.
  """
  path = _FromString(path_str)

  # Validate that the path is of the right type of the type is specified.
  if path_type and path.type != path_type:
    raise ValueError(
        'Unexpected path type. Expected: %s, actual: %s. Path: %s'
        % (path_type, path.type, path_str)
    )

  return path


def FromPath(
    base_path: Path,
    store_id: Optional[str] = None,
    study_uid: Optional[str] = None,
    series_uid: Optional[str] = None,
    instance_uid: Optional[str] = None,
) -> Path:
  """Creates a new Path object based on the provided one.

  Replaces the specified path components in the base path to create the new one.

  Args:
    base_path: The base path to use.
    store_id: The store ID to use in the new path or None if the store ID from
      the base path should be used.
    study_uid: The study UID to use in the new path or None if the study UID
      from the base path should be used.
    series_uid: The series UID to use in the new path or None if the series UID
      from the base path should be used.
    instance_uid: The instance UID to use in the new path or None if the
      instance UID from the base path should be used.

  Returns:
    The newly constructed Path object.
  Raises:
    ValueError if the new path is invalid (e.g. if the instance UID is
      specified, but the series UID is None).
  """
  store_id = store_id if store_id else base_path.store_id
  study_uid = study_uid if study_uid else base_path.study_uid
  series_uid = series_uid if series_uid else base_path.series_uid
  instance_uid = instance_uid if instance_uid else base_path.instance_uid
  return Path(
      base_path.project_id,
      base_path.location,
      base_path.dataset_id,
      store_id,
      study_uid,
      series_uid,
      instance_uid,
  )
