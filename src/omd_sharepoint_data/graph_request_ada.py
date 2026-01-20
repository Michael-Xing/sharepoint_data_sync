#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
@version: 1.0.0
@author: mingbo.xing
@contact: mingbo.xing@daocloud.io
@time: 2026/1/20 10:11
"""
from typing import Dict, Optional
import httpx
from kiota_abstractions.authentication import AuthenticationProvider
from kiota_http.middleware.options import UrlReplaceHandlerOption
from msgraph_core import APIVersion, BaseGraphRequestAdapter, GraphClientFactory, NationalClouds
from msgraph_core.middleware.options import GraphTelemetryHandlerOption

VERSION = "1.2.0"

options = {
    UrlReplaceHandlerOption.get_key(): UrlReplaceHandlerOption(
        enabled = True,
        replacement_pairs = {"/users/me-token-to-replace": "/me"}
    ),
    GraphTelemetryHandlerOption.get_key(): GraphTelemetryHandlerOption(
        api_version=APIVersion.v1,
        sdk_version=VERSION)
}


class GraphRequestAdapter(BaseGraphRequestAdapter):
    # host：改成中国区
    def __init__(self, auth_provider: AuthenticationProvider,
                 client: Optional[httpx.AsyncClient] = GraphClientFactory.create_with_default_middleware(host=NationalClouds.China,options=options)) -> None:
        super().__init__(auth_provider, http_client=client)
