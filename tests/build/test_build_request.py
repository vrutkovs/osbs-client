"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import copy
import json
import os
from pkg_resources import parse_version
import shutil

from osbs.build.build_request import BuildManager, BuildRequest, ProductionBuild
from osbs.constants import (PROD_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE,
                            PROD_WITH_SECRET_BUILD_TYPE)
from osbs.exceptions import OsbsValidationException

from flexmock import flexmock
import pytest

from tests.constants import (INPUTS_PATH, TEST_BUILD_CONFIG, TEST_BUILD_JSON, TEST_COMPONENT,
                             TEST_GIT_BRANCH, TEST_GIT_REF, TEST_GIT_URI)


class NoSuchPluginException(Exception):
    pass


def get_plugin(plugins, plugin_type, plugin_name):
    plugins = plugins[plugin_type]
    for plugin in plugins:
        if plugin["name"] == plugin_name:
            return plugin
    else:
        raise NoSuchPluginException()


def plugin_value_get(plugins, plugin_type, plugin_name, *args):
    result = get_plugin(plugins, plugin_type, plugin_name)
    for arg in args:
        result = result[arg]
    return result


class TestBuildRequest(object):
    def test_build_request_is_auto_instantiated(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        br = BuildRequest('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.is_auto_instantiated() is True

    def test_build_request_isnt_auto_instantiated(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        build_json['spec']['triggers'] = []
        br = BuildRequest('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.is_auto_instantiated() is False

    def test_render_simple_request_incorrect_postbuild(self, tmpdir):
        # Make temporary copies of the JSON files
        for basename in ['simple.json', 'simple_inner.json']:
            shutil.copy(os.path.join(INPUTS_PATH, basename),
                        os.path.join(str(tmpdir), basename))

        # Create an inner JSON description which incorrectly runs the exit
        # plugins as postbuild plugins.
        with open(os.path.join(str(tmpdir), 'simple_inner.json'), 'r+') as inner:
            inner_json = json.load(inner)

            # Re-write all the exit plugins as postbuild plugins
            exit_plugins = inner_json['exit_plugins']
            inner_json['postbuild_plugins'].extend(exit_plugins)
            del inner_json['exit_plugins']

            inner.seek(0)
            json.dump(inner_json, inner)
            inner.truncate()

        bm = BuildManager(str(tmpdir))
        build_request = bm.get_build_request_by_type("simple")
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': "component",
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        # Check the store_metadata_in_osv3's uri parameter was set
        # correctly, even though it was listed as a postbuild plugin.
        assert plugin_value_get(plugins, "postbuild_plugins", "store_metadata_in_osv3", "args", "url") == \
            "http://openshift/"

    @pytest.mark.parametrize('tag', [
        None,
        "some_tag",
    ])
    @pytest.mark.parametrize('registry_uris', [
        [],
        ["registry.example.com:5000"],
        ["registry.example.com:5000", "localhost:6000"],
    ])
    def test_render_simple_request(self, tag, registry_uris):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type("simple")
        name_label = "fedora/resultingimage"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uris': registry_uris,
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'tag': tag,
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] is not None
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF

        expected_output = "john-foo/component:%s" % (tag if tag else "20")
        if registry_uris:
            expected_output = registry_uris[0] + "/" + expected_output
        assert build_json["spec"]["output"]["to"]["name"].startswith(expected_output)

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)
        pull_base_image = get_plugin(plugins, "prebuild_plugins",
                                     "pull_base_image")
        assert pull_base_image is not None
        assert ('args' not in pull_base_image or
                'parent_registry' not in pull_base_image['args'])

        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3", "args", "url") == \
            "http://openshift/"

        for r in registry_uris:
            assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                    "registries", r) == {"insecure": True}

    @pytest.mark.parametrize('architecture', [
        None,
        'x86_64',
    ])
    def test_render_prod_request_with_repo(self, architecture):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        name_label = "fedora/resultingimage"
        assert isinstance(build_request, ProductionBuild)
        push_url = 'ssh://git.example.com/git/{0}.git'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': architecture,
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'yum_repourls': ["http://example.com/my.repo"],
            'git_push_url': push_url.format(TEST_COMPONENT),
            'registry_api_versions': ['v1'],
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] == TEST_BUILD_CONFIG
        assert "triggers" in build_json["spec"]
        assert build_json["spec"]["triggers"][0]\
            ["imageChange"]["from"]["name"] == 'fedora:latest'
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_BRANCH
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        assert get_plugin(plugins, "prebuild_plugins",
                          "stop_autorebuild_if_disabled")
        assert get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image",
                                "args", "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert get_plugin(plugins, "exit_plugins", "koji_promote")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "sendmail")
        assert 'sourceSecret' not in build_json["spec"]["source"]

        assert plugin_value_get(plugins, "prebuild_plugins", "add_yum_repo_by_url",
                                "args", "repourls") == ["http://example.com/my.repo"]

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['Authoritative_Registry'] is not None
        assert labels['Build_Host'] is not None
        assert labels['Vendor'] is not None
        assert labels['distribution-scope'] is not None
        if architecture:
            assert labels['Architecture'] is not None
        else:
            assert 'Architecture' not in labels

    @pytest.mark.parametrize(('registry_uri', 'insecure_registry'), [
        ("https://registry.example.com", False),
        ("http://registry.example.com", True),
    ])
    @pytest.mark.parametrize('params', [
        # Wrong way round
        {
            'git_ref': TEST_GIT_BRANCH,
            'git_branch': TEST_GIT_REF,
            'should_raise': True,
        },

        # Right way round
        {
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'should_raise': False,
        },
    ])
    def test_render_prod_request(self, registry_uri, insecure_registry, params):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        # We're using both pulp and sendmail, both of which require a
        # Kubernetes secret. This isn't supported until OpenShift
        # Origin 1.0.6.
        build_request.set_openshift_required_version(parse_version('1.0.6'))
        push_url = "ssh://{username}git.example.git/git/{component}.git"
        name_label = "fedora/resultingimage"
        pdc_secret_name = 'foo'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': params['git_ref'],
            'git_branch': params['git_branch'],
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': registry_uri,
            'source_registry_uri': registry_uri,
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1'],
            'pdc_secret': pdc_secret_name,
            'pdc_url': 'https://pdc.example.com',
            'smtp_uri': 'smtp.example.com',
            'git_push_url': push_url.format(username='',
                                            component=TEST_COMPONENT),
            'git_push_username': 'example',
        }
        build_request.set_params(**kwargs)
        if params['should_raise']:
            with pytest.raises(OsbsValidationException):
                build_request.render()

            return

        build_json = build_request.render()
        assert build_json["metadata"]["name"] == TEST_BUILD_CONFIG
        assert "triggers" in build_json["spec"]
        assert build_json["spec"]["triggers"][0]\
            ["imageChange"]["from"]["name"] == 'fedora:latest'
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_BRANCH
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        assert get_plugin(plugins, "prebuild_plugins",
                          "stop_autorebuild_if_disabled")
        assert plugin_value_get(plugins, "prebuild_plugins",
                                "check_and_set_rebuild", "args",
                                "url") == kwargs["openshift_uri"]
        assert get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "bump_release",
                                "args", "git_ref") == TEST_GIT_REF
        assert plugin_value_get(plugins, "prebuild_plugins",
                                "bump_release", "args",
                                "push_url") == push_url.format(username='example@',
                                                               component=TEST_COMPONENT)

        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args",
                                "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "root") == "http://root/"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "target") == "koji-target"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "hub") == "http://hub/"
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")

        assert get_plugin(plugins, "exit_plugins", "koji_promote")
        assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                "args", "kojihub") == kwargs["kojihub"]
        assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                "args", "url") == kwargs["openshift_uri"]

        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert plugin_value_get(plugins, "postbuild_plugins", "import_image",
                                "args", "imagestream") == name_label.replace('/', '-')
        expected_repo = os.path.join(kwargs["registry_uri"], name_label)
        expected_repo = expected_repo.replace('https://', '')
        expected_repo = expected_repo.replace('http://', '')
        assert plugin_value_get(plugins, "postbuild_plugins", "import_image",
                                "args", "docker_image_repo") == expected_repo
        assert plugin_value_get(plugins, "postbuild_plugins", "import_image",
                                "args", "url") == kwargs["openshift_uri"]
        if insecure_registry:
            assert plugin_value_get(plugins,
                                    "postbuild_plugins", "import_image", "args",
                                    "insecure_registry")
        else:
            with pytest.raises(KeyError):
                plugin_value_get(plugins,
                                 "postbuild_plugins", "import_image", "args",
                                 "insecure_registry")

        assert get_plugin(plugins, "exit_plugins", "sendmail")
        assert 'sourceSecret' not in build_json["spec"]["source"]

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['Architecture'] is not None
        assert labels['Authoritative_Registry'] is not None
        assert labels['Build_Host'] is not None
        assert labels['Vendor'] is not None
        assert labels['distribution-scope'] is not None

        pdc_secret = [secret for secret in
                      build_json['spec']['strategy']['customStrategy']['secrets']
                      if secret['secretSource']['name'] == pdc_secret_name]
        mount_path = pdc_secret[0]['mountPath']
        expected = {'args': {'from_address': 'osbs@example.com',
                             'url': 'http://openshift/',
                             'pdc_url': 'https://pdc.example.com',
                             'pdc_secret_path': mount_path,
                             'send_on': ['auto_fail', 'auto_success'],
                             'error_addresses': ['errors@example.com'],
                             'smtp_uri': 'smtp.example.com',
                             'submitter': 'john-foo'},
                    'name': 'sendmail'}
        assert get_plugin(plugins, 'exit_plugins', 'sendmail') == expected

    def test_render_prod_without_koji_request(self):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_WITHOUT_KOJI_BUILD_TYPE)
        name_label = "fedora/resultingimage"
        assert isinstance(build_request, ProductionBuild)
        push_url = 'ssh://git.example.com/git/{0}.git'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'git_push_url': push_url.format(TEST_COMPONENT),
            'registry_api_versions': ['v1'],
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] == TEST_BUILD_CONFIG
        assert "triggers" in build_json["spec"]
        assert build_json["spec"]["triggers"][0]\
            ["imageChange"]["from"]["name"] == 'fedora:latest'
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_BRANCH
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:none-"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        assert get_plugin(plugins, "prebuild_plugins",
                          "stop_autorebuild_if_disabled")
        assert get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args",
                                "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "koji_promote")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "sendmail")
        assert 'sourceSecret' not in build_json["spec"]["source"]

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['Architecture'] is not None
        assert labels['Authoritative_Registry'] is not None
        assert labels['Build_Host'] is not None
        assert labels['Vendor'] is not None
        assert labels['distribution-scope'] is not None

    def test_render_prod_with_secret_request(self):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_WITH_SECRET_BUILD_TYPE)
        assert isinstance(build_request, ProductionBuild)
        push_url = 'ssh://git.example.com/git/{0}.git'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "",
            'pulp_registry': "registry.example.com",
            'nfs_server_path': "server:path",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'git_push_url': push_url.format(TEST_COMPONENT),
            'registry_api_versions': ['v1'],
            'source_secret': 'mysecret',
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert "triggers" in build_json["spec"]
        assert build_json["spec"]["triggers"][0]\
            ["imageChange"]["from"]["name"] == 'fedora:latest'

        assert build_json["spec"]["source"]["sourceSecret"]["name"] == "mysecret"

        strategy = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in strategy:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        assert get_plugin(plugins, "prebuild_plugins",
                          "stop_autorebuild_if_disabled")
        assert get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert get_plugin(plugins, "prebuild_plugins", "koji")
        assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        assert get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert get_plugin(plugins, "exit_plugins", "koji_promote")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "sendmail")
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries") == {}

    def test_render_prod_request_requires_newer(self):
        """
        We should get an OsbsValidationException when trying to use the
        sendmail plugin without requiring OpenShift 1.0.6, as
        configuring the plugin requires the new-style secrets.
        """
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_WITH_SECRET_BUILD_TYPE)
        build_request.set_openshift_required_version(parse_version('0.5.4'))
        name_label = "fedora/resultingimage"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uris': ["registry1.example.com/v1",  # first is primary
                              "registry2.example.com/v2"],
            'nfs_server_path': "server:path",
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'pdc_secret': 'foo',
            'pdc_url': 'https://pdc.example.com',
            'smtp_uri': 'smtp.example.com',
        }
        build_request.set_params(**kwargs)
        with pytest.raises(OsbsValidationException):
            build_request.render()

    @pytest.mark.parametrize('registry_api_versions', [
        ['v1', 'v2'],
        ['v2'],
    ])
    @pytest.mark.parametrize('openshift_version', ['1.0.0', '1.0.6'])
    def test_render_prod_request_v1_v2(self, registry_api_versions, openshift_version):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_WITH_SECRET_BUILD_TYPE)
        build_request.set_openshift_required_version(parse_version(openshift_version))
        name_label = "fedora/resultingimage"
        pulp_env = 'v1pulp'
        pulp_secret = pulp_env + 'secret'
        kwargs = {
            'pulp_registry': pulp_env,
            'pulp_secret': pulp_secret,
        }

        push_url = 'ssh://git.example.com/git/{0}.git'
        kwargs.update({
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uris': [
                # first is primary
                "http://registry1.example.com:5000/v1",

                "http://registry2.example.com:5000/v2"
            ],
            'nfs_server_path': "server:path",
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'git_push_url': push_url.format(TEST_COMPONENT),
            'registry_api_versions': registry_api_versions,
        })
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] == TEST_BUILD_CONFIG
        assert "triggers" in build_json["spec"]
        assert build_json["spec"]["triggers"][0]\
            ["imageChange"]["from"]["name"] == 'fedora:latest'
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_BRANCH

        # Pulp used, so no direct registry output
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "john-foo/component:"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        # tag_and_push configuration. Must not have the scheme part.
        expected_registries = {
            'registry2.example.com:5000': {'insecure': True},
        }

        if 'v1' in registry_api_versions:
            expected_registries['registry1.example.com:5000'] = {
                'insecure': True,
            }

        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push",
                                "args", "registries") == expected_registries

        if openshift_version == '1.0.0':
            assert 'secrets' not in build_json['spec']['strategy']['customStrategy']
            assert build_json['spec']['source']['sourceSecret']['name'] == pulp_secret
        else:
            assert 'sourceSecret' not in build_json['spec']['source']
            secrets = build_json['spec']['strategy']['customStrategy']['secrets']
            for version, plugin in [('v1', 'pulp_push'), ('v2', 'pulp_sync')]:
                if version not in registry_api_versions:
                    continue

                path = plugin_value_get(plugins, "postbuild_plugins", plugin,
                                        "args", "pulp_secret_path")
                pulp_secrets = [secret for secret in secrets if secret['mountPath'] == path]
                assert len(pulp_secrets) == 1
                assert pulp_secrets[0]['secretSource']['name'] == pulp_secret

        if 'v1' in registry_api_versions:
            assert get_plugin(plugins, "postbuild_plugins",
                              "compress")
            assert get_plugin(plugins, "postbuild_plugins",
                              "cp_built_image_to_nfs")
            assert get_plugin(plugins, "postbuild_plugins",
                              "pulp_push")
            assert plugin_value_get(plugins, "postbuild_plugins", "pulp_push",
                                    "args", "pulp_registry_name") == pulp_env
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins",
                           "compress")
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins",
                           "cp_built_image_to_nfs")
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins",
                           "pulp_push")

        if 'v2' in registry_api_versions:
            assert get_plugin(plugins, "postbuild_plugins", "pulp_sync")
            env = plugin_value_get(plugins, "postbuild_plugins", "pulp_sync",
                                   "args", "pulp_registry_name")
            assert env == pulp_env

            docker_registry = plugin_value_get(plugins, "postbuild_plugins",
                                               "pulp_sync", "args",
                                               "docker_registry")

            # pulp_sync config must have the scheme part to satisfy pulp.
            assert docker_registry == 'http://registry2.example.com:5000'
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins", "pulp_sync")

    def test_render_with_yum_repourls(self):
        bm = BuildManager(INPUTS_PATH)
        push_url = 'ssh://git.example.com/git/{0}.git'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'git_push_url': push_url.format(TEST_COMPONENT),
            'registry_api_versions': ['v1'],
        }
        build_request = bm.get_build_request_by_type("prod")

        # Test validation for yum_repourls parameter
        kwargs['yum_repourls'] = 'should be a list'
        with pytest.raises(OsbsValidationException):
            build_request.set_params(**kwargs)

        # Use a valid yum_repourls parameter and check the result
        kwargs['yum_repourls'] = ['http://example.com/repo1.repo', 'http://example.com/repo2.repo']
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert "triggers" in build_json["spec"]
        assert build_json["spec"]["triggers"][0]\
            ["imageChange"]["from"]["name"] == 'fedora:latest'

        strategy = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in strategy:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        repourls = None
        for d in plugins['prebuild_plugins']:
            if d['name'] == 'add_yum_repo_by_url':
                repourls = d['args']['repourls']

        assert repourls is not None
        assert len(repourls) == 2
        assert 'http://example.com/repo1.repo' in repourls
        assert 'http://example.com/repo2.repo' in repourls

        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        assert get_plugin(plugins, "prebuild_plugins",
                          "stop_autorebuild_if_disabled")
        assert get_plugin(plugins, "prebuild_plugins", "bump_release")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert get_plugin(plugins, "exit_plugins", "koji_promote")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "sendmail")

    def test_render_prod_with_pulp_no_auth(self):
        """
        Rendering should fail if pulp is specified but auth config isn't
        """
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'pulp_registry': "foo",
        }
        build_request.set_params(**kwargs)
        with pytest.raises(OsbsValidationException):
            build_request.render()

    @staticmethod
    def create_no_image_change_trigger_json(outdir):
        """
        Create JSON templates with an image change trigger added.

        :param outdir: str, path to store modified templates
        """

        # Make temporary copies of the JSON files
        for basename in ['prod.json', 'prod_inner.json']:
            shutil.copy(os.path.join(INPUTS_PATH, basename),
                        os.path.join(outdir, basename))

        # Create a build JSON description with an image change trigger
        with open(os.path.join(outdir, 'prod.json'), 'r+') as prod_json:
            build_json = json.load(prod_json)

            # Remove the image change trigger
            del build_json['spec']['triggers']
            prod_json.seek(0)
            json.dump(build_json, prod_json)
            prod_json.truncate()

    def test_render_prod_request_without_trigger(self, tmpdir):
        self.create_no_image_change_trigger_json(str(tmpdir))
        bm = BuildManager(str(tmpdir))
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        name_label = "fedora/resultingimage"
        push_url = "ssh://{username}git.example.com/git/{component}.git"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1'],
            'git_push_url': push_url.format(username='', component=TEST_COMPONENT),
            'git_push_username': 'example',
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF

        strategy = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in strategy:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        plugins = json.loads(plugins_json)
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "import_image")
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "koji_promote")

    @pytest.mark.parametrize('missing', [
        'git_branch',
        'git_push_url',
    ])
    def test_render_prod_request_trigger_missing_param(self, tmpdir, missing):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        push_url = "ssh://{username}git.example.com/git/{component}.git"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1'],
            'git_push_url': push_url.format(username='', component=TEST_COMPONENT),
            'git_push_username': 'example',
        }

        # Remove one of the parameters required for rebuild triggers
        del kwargs[missing]

        build_request.set_params(**kwargs)
        build_json = build_request.render()

        # Verify the triggers are now disabled
        assert "triggers" not in build_json["spec"]

        strategy = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in strategy:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        # Verify the rebuild plugins are all disabled
        plugins = json.loads(plugins_json)
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "import_image")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "koji_promote")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "sendmail")

    def test_render_prod_request_new_secrets(self, tmpdir):
        bm = BuildManager(INPUTS_PATH)
        secret_name = 'mysecret'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': "fedora/resultingimage",
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1'],
            'pulp_registry': 'foo',
            'pulp_secret': secret_name,
        }

        # Default required version (0.5.4), implicitly and explicitly
        for required in (None, parse_version('0.5.4')):
            build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
            if required is not None:
                build_request.set_openshift_required_version(required)

            build_request.set_params(**kwargs)
            build_json = build_request.render()

            # Using the sourceSecret scheme
            assert 'sourceSecret' in build_json['spec']['source']
            assert build_json['spec']['source']\
                ['sourceSecret']['name'] == secret_name

            # Not using the secrets array scheme
            assert 'secrets' not in build_json['spec']['strategy']['customStrategy']

            # We shouldn't have pulp_secret_path set
            env = build_json['spec']['strategy']['customStrategy']['env']
            plugins_json = None
            for d in env:
                if d['name'] == 'DOCK_PLUGINS':
                    plugins_json = d['value']
                    break

            assert plugins_json is not None
            plugins = json.loads(plugins_json)
            assert 'pulp_secret_path' not in plugin_value_get(plugins,
                                                              'postbuild_plugins',
                                                              'pulp_push',
                                                              'args')

        # Set required version to 1.0.6

        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        build_request.set_openshift_required_version(parse_version('1.0.6'))
        build_json = build_request.render()
        # Not using the sourceSecret scheme
        assert 'sourceSecret' not in build_json['spec']['source']

        # Using the secrets array scheme instead
        assert 'secrets' in build_json['spec']['strategy']['customStrategy']
        secrets = build_json['spec']['strategy']['customStrategy']['secrets']
        pulp_secret = [secret for secret in secrets
                       if secret['secretSource']['name'] == secret_name]
        assert len(pulp_secret) > 0
        assert 'mountPath' in pulp_secret[0]

        # Check that the secret's mountPath matches the plugin's
        # configured path for the secret
        mount_path = pulp_secret[0]['mountPath']
        env = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)
        assert plugin_value_get(plugins, 'postbuild_plugins', 'pulp_push',
                                'args', 'pulp_secret_path') == mount_path
