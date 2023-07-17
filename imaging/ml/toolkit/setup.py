# Copyright 2019 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Setup file for Healthcare Imaging ML Toolkit."""

from setuptools import find_packages
from setuptools import setup

REQUIRED_PACKAGES = [
    'absl-py',
    'google-auth-httplib2',
    'httplib2',
    'urllib3',
    'retrying',
    'requests-toolbelt',
    'numpy',
    'mock',
    'google-auth',
    'attrs',
]

setup(
    name='hcls_imaging_ml_toolkit_ez_wsi',
    version='1.0.0',
    install_requires=REQUIRED_PACKAGES,
    python_requires='>=3',
    packages=find_packages(),
    author='Google',
    author_email='noreply@google.com',
    license='Apache 2.0',
    url='https://github.com/armantajback/healthcare',
    description='Fork of Toolkit for deploying ML models on GCP leveraging Google Cloud Healthcare API.'
)
