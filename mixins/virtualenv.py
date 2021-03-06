class VirtualenvMixin(object):
    '''BaseScript mixin, designed to create and use virtualenvs.

    Config items:
     * virtualenv_path points to the virtualenv location on disk.
     * virtualenv_modules lists the module names.
     * MODULE_url list points to the module URLs (optional)
    Requires virtualenv to be in PATH.
    Depends on ScriptMixin
    '''
    python_paths = {}
    site_packages_path = None

    def __init__(self, *args, **kwargs):
        self._virtualenv_modules = []
        super(VirtualenvMixin, self).__init__(*args, **kwargs)

    def register_virtualenv_module(self, name=None, url=None, method=None,
                                   requirements=None, optional=False,
                                   two_pass=False, editable=False):
        """Register a module to be installed with the virtualenv.

        This method can be called up until create_virtualenv() to register
        modules that should be installed in the virtualenv.

        See the documentation for install_module for how the arguments are
        applied.
        """
        self._virtualenv_modules.append((name, url, method, requirements,
                                         optional, two_pass, editable))

    def query_virtualenv_path(self):
        c = self.config
        dirs = self.query_abs_dirs()
        virtualenv = None
        if 'abs_virtualenv_dir' in dirs:
            virtualenv = dirs['abs_virtualenv_dir']
        elif c.get('virtualenv_path'):
            if os.path.isabs(c['virtualenv_path']):
                virtualenv = c['virtualenv_path']
            else:
                virtualenv = os.path.join(dirs['abs_work_dir'],
                                          c['virtualenv_path'])
        return virtualenv

    def query_python_path(self, binary="python"):
        """Return the path of a binary inside the virtualenv, if
        c['virtualenv_path'] is set; otherwise return the binary name.
        Otherwise return None
        """
        if binary not in self.python_paths:
            bin_dir = 'bin'
            if self._is_windows():
                bin_dir = 'Scripts'
            virtualenv_path = self.query_virtualenv_path()
            if virtualenv_path:
                self.python_paths[binary] = os.path.abspath(os.path.join(virtualenv_path, bin_dir, binary))
            else:
                self.python_paths[binary] = self.query_exe(binary)
        return self.python_paths[binary]

    def query_python_site_packages_path(self):
        if self.site_packages_path:
            return self.site_packages_path
        python = self.query_python_path()
        self.site_packages_path = self.get_output_from_command(
            [python, '-c',
             'from distutils.sysconfig import get_python_lib; ' +
             'print(get_python_lib())'])
        return self.site_packages_path

    def package_versions(self, pip_freeze_output=None, error_level=WARNING, log_output=False):
        """
        reads packages from `pip freeze` output and returns a dict of
        {package_name: 'version'}
        """
        packages = {}

        if pip_freeze_output is None:
            # get the output from `pip freeze`
            pip = self.query_python_path("pip")
            if not pip:
                self.log("package_versions: Program pip not in path", level=error_level)
                return {}
            pip_freeze_output = self.get_output_from_command([pip, "freeze"], silent=True)
            if not isinstance(pip_freeze_output, basestring):
                self.fatal("package_versions: Error encountered running `pip freeze`: %s" % pip_freeze_output)

        for line in pip_freeze_output.splitlines():
            # parse the output into package, version
            line = line.strip()
            if not line:
                # whitespace
                continue
            if line.startswith('-'):
                # not a package, probably like '-e http://example.com/path#egg=package-dev'
                continue
            if '==' not in line:
                self.fatal("pip_freeze_packages: Unrecognized output line: %s" % line)
            package, version = line.split('==', 1)
            packages[package] = version

        if log_output:
            self.info("Current package versions:")
            for package in packages:
                self.info("  %s == %s" % (package, packages[package]))

        return packages

    def is_python_package_installed(self, package_name, error_level=WARNING):
        """
        Return whether the package is installed
        """
        packages = self.package_versions(error_level=error_level).keys()
        return package_name.lower() in [package.lower() for package in packages]

    def install_module(self, module=None, module_url=None, install_method=None,
                       requirements=(), optional=False, global_options=[],
                       no_deps=False, editable=False):
        """
        Install module via pip.

        module_url can be a url to a python package tarball, a path to
        a directory containing a setup.py (absolute or relative to work_dir)
        or None, in which case it will default to the module name.

        requirements is a list of pip requirements files.  If specified, these
        will be combined with the module_url (if any), like so:

        pip install -r requirements1.txt -r requirements2.txt module_url
        """
        c = self.config
        dirs = self.query_abs_dirs()
        venv_path = self.query_virtualenv_path()
        self.info("Installing %s into virtualenv %s" % (module, venv_path))
        if not module_url:
            module_url = module
        if install_method in (None, 'pip'):
            if not module_url and not requirements:
                self.fatal("Must specify module and/or requirements")
            pip = self.query_python_path("pip")
            if c.get("verbose_pip"):
                command = [pip, "-v", "install"]
            else:
                command = [pip, "install"]
            if no_deps:
                command += ["--no-deps"]
            virtualenv_cache_dir = c.get("virtualenv_cache_dir", os.path.join(venv_path, "cache"))
            if virtualenv_cache_dir:
                command += ["--download-cache", virtualenv_cache_dir]
            # To avoid timeouts with our pypi server, increase default timeout:
            # https://bugzilla.mozilla.org/show_bug.cgi?id=1007230#c802
            command += ['--timeout', str(c.get('pip_timeout', 120))]
            for requirement in requirements:
                command += ["-r", requirement]
            if c.get('find_links') and not c["pip_index"]:
                command += ['--no-index']
            for opt in global_options:
                command += ["--global-option", opt]
        elif install_method == 'easy_install':
            if not module:
                self.fatal("module parameter required with install_method='easy_install'")
            if requirements:
                # Install pip requirements files separately, since they're
                # not understood by easy_install.
                self.install_module(requirements=requirements,
                                    install_method='pip')
            # Allow easy_install to be overridden by
            # self.config['exes']['easy_install']
            default = 'easy_install'
            if self._is_windows():
                # Don't invoke `easy_install` directly on windows since
                # the 'install' in the executable name hits UAC
                # - http://answers.microsoft.com/en-us/windows/forum/windows_7-security/uac-message-do-you-want-to-allow-the-following/bea30ad8-9ef8-4897-aab4-841a65f7af71
                # - https://bugzilla.mozilla.org/show_bug.cgi?id=791840
                default = [self.query_python_path(), self.query_python_path('easy_install-script.py')]
            command = self.query_exe('easy_install', default=default, return_type="list")
        else:
            self.fatal("install_module() doesn't understand an install_method of %s!" % install_method)

        # Add --find-links pages to look at
        proxxy = Proxxy(self.config, self.log_obj)
        for link in proxxy.get_proxies_and_urls(c.get('find_links', [])):
            command.extend(["--find-links", link])

        # module_url can be None if only specifying requirements files
        if module_url:
            if editable:
                if install_method in (None, 'pip'):
                    command += ['-e']
                else:
                    self.fatal("editable installs not supported for install_method %s" % install_method)
            command += [module_url]

        # If we're only installing a single requirements file, use
        # the file's directory as cwd, so relative paths work correctly.
        cwd = dirs['abs_work_dir']
        if not module and len(requirements) == 1:
            cwd = os.path.dirname(requirements[0])

        quoted_command = subprocess.list2cmdline(command)
        # Allow for errors while building modules, but require a
        # return status of 0.
        self.retry(
            self.run_command,
            # None will cause default value to be used
            attempts=1 if optional else None,
            good_statuses=(0,),
            error_level=WARNING if optional else FATAL,
            error_message='Could not install python package: ' + quoted_command + ' failed after %(attempts)d tries!',
            args=[command, ],
            kwargs={
                'error_list': VirtualenvErrorList,
                'cwd': cwd,
                # WARNING only since retry will raise final FATAL if all
                # retry attempts are unsuccessful - and we only want
                # an ERROR of FATAL if *no* retry attempt works
                'error_level': WARNING,
            }
        )

    def create_virtualenv(self, modules=(), requirements=()):
        """
        Create a python virtualenv.

        The virtualenv exe can be defined in c['virtualenv'] or
        c['exes']['virtualenv'], as a string (path) or list (path +
        arguments).

        c['virtualenv_python_dll'] is an optional config item that works
        around an old windows virtualenv bug.

        virtualenv_modules can be a list of module names to install, e.g.

            virtualenv_modules = ['module1', 'module2']

        or it can be a heterogeneous list of modules names and dicts that
        define a module by its name, url-or-path, and a list of its global
        options.

            virtualenv_modules = [
                {
                    'name': 'module1',
                    'url': None,
                    'global_options': ['--opt', '--without-gcc']
                },
                {
                    'name': 'module2',
                    'url': 'http://url/to/package',
                    'global_options': ['--use-clang']
                },
                {
                    'name': 'module3',
                    'url': os.path.join('path', 'to', 'setup_py', 'dir')
                    'global_options': []
                },
                'module4'
            ]

        virtualenv_requirements is an optional list of pip requirements files to
        use when invoking pip, e.g.,

            virtualenv_requirements = [
                '/path/to/requirements1.txt',
                '/path/to/requirements2.txt'
            ]
        """
        c = self.config
        dirs = self.query_abs_dirs()
        venv_path = self.query_virtualenv_path()
        self.info("Creating virtualenv %s" % venv_path)
        virtualenv = c.get('virtualenv', self.query_exe('virtualenv'))
        if isinstance(virtualenv, str):
            # allow for [python, virtualenv] in config
            virtualenv = [virtualenv]

        if not os.path.exists(virtualenv[0]) and not self.which(virtualenv[0]):
            self.add_summary("The executable '%s' is not found; not creating "
                             "virtualenv!" % virtualenv[0], level=FATAL)
            return -1

        # https://bugs.launchpad.net/virtualenv/+bug/352844/comments/3
        # https://bugzilla.mozilla.org/show_bug.cgi?id=700415#c50
        if c.get('virtualenv_python_dll'):
            # We may someday want to copy a differently-named dll, but
            # let's not think about that right now =\
            dll_name = os.path.basename(c['virtualenv_python_dll'])
            target = self.query_python_path(dll_name)
            scripts_dir = os.path.dirname(target)
            self.mkdir_p(scripts_dir)
            self.copyfile(c['virtualenv_python_dll'], target, error_level=WARNING)
        else:
            self.mkdir_p(dirs['abs_work_dir'])

        # make this list configurable?
        for module in ('distribute', 'pip'):
            if c.get('%s_url' % module):
                self.download_file(c['%s_url' % module],
                                   parent_dir=dirs['abs_work_dir'])

        virtualenv_options = c.get('virtualenv_options',
                                   ['--no-site-packages', '--distribute'])

        if os.path.exists(self.query_python_path()):
            self.info("Virtualenv %s appears to already exist; skipping virtualenv creation." % self.query_python_path())
        else:
            self.run_command(virtualenv + virtualenv_options + [venv_path],
                             cwd=dirs['abs_work_dir'],
                             error_list=VirtualenvErrorList,
                             halt_on_failure=True)
        if not modules:
            modules = c.get('virtualenv_modules', [])
        if not requirements:
            requirements = c.get('virtualenv_requirements', [])
        if not modules and requirements:
            self.install_module(requirements=requirements,
                                install_method='pip')
        for module in modules:
            module_url = module
            global_options = []
            if isinstance(module, dict):
                if module.get('name', None):
                    module_name = module['name']
                else:
                    self.fatal("Can't install module without module name: %s" %
                               str(module))
                module_url = module.get('url', None)
                global_options = module.get('global_options', [])
            else:
                module_url = self.config.get('%s_url' % module, module_url)
                module_name = module
            install_method = 'pip'
            if module_name in ('pywin32',):
                install_method = 'easy_install'
            self.install_module(module=module_name,
                                module_url=module_url,
                                install_method=install_method,
                                requirements=requirements,
                                global_options=global_options)

        for module, url, method, requirements, optional, two_pass, editable in \
                self._virtualenv_modules:
            if two_pass:
                self.install_module(
                    module=module, module_url=url,
                    install_method=method, requirements=requirements or (),
                    optional=optional, no_deps=True, editable=editable
                )
            self.install_module(
                module=module, module_url=url,
                install_method=method, requirements=requirements or (),
                optional=optional, editable=editable
            )

        self.info("Done creating virtualenv %s." % venv_path)

        self.package_versions(log_output=True)

    def activate_virtualenv(self):
        """Import the virtualenv's packages into this Python interpreter."""
        bin_dir = os.path.dirname(self.query_python_path())
        activate = os.path.join(bin_dir, 'activate_this.py')
        execfile(activate, dict(__file__=activate))

