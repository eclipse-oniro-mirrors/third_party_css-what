#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Copy the owned runtime source directory and track its actual membership."""
import argparse
import os
from pathlib import Path
import shutil
import tempfile
import time


def source_dependencies(source):
    dependencies = set()

    def fail(error):
        raise error

    for directory, children, files in os.walk(source, followlinks=True, onerror=fail):
        path = Path(directory)
        dependencies.add(path)
        parents = [path, *path.parents[:len(path.relative_to(source).parts)]]
        for child in children:
            candidate = path/child
            if candidate.is_symlink() and candidate.resolve() in {p.resolve() for p in parents}:
                raise ValueError('Directory link cycle in CSS sources: '+str(candidate))
        dependencies.update(path/name for name in files)
    return dependencies


def escape(value):
    if '\n' in value or '\r' in value:
        raise ValueError('A dependency path contains a newline')
    return (value.replace('\\', '\\\\').replace('$', '$$')
            .replace(' ', '\\ ').replace('\t', '\\\t')
            .replace('#', '\\#').replace(':', '\\:'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--depfile', type=Path, required=True)
    parser.add_argument('--exported-output', type=Path)
    parser.add_argument('--dep-target', required=True)
    args = parser.parse_args()
    source, output = args.source.absolute(), args.output.absolute()
    if not source.is_dir():
        raise FileNotFoundError(source)
    started_ns = time.time_ns()
    dependencies = source_dependencies(source)
    dependencies.add(Path(__file__).absolute())
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.css-copy-') as temporary:
        staged = Path(temporary)/'src'
        shutil.copytree(source, staged)
        # This target owns the whole source directory. Downstream JSFramework
        # copies it into its separate temporary runtime and must not modify it.
        if output.is_symlink():
            output.unlink()
        elif output.is_dir():
            shutil.rmtree(output)
        elif output.exists():
            output.unlink()
        os.replace(staged, output)
    generated_files = [path for path in output.rglob('*') if path.is_file()]
    dependencies.update(generated_files)
    if args.exported_output is not None:
        # A downstream copy owns this public payload. Track its members so a
        # removed exported file refreshes staging and schedules that copy again.
        dependencies.update(args.exported_output.absolute()/path.relative_to(output)
                            for path in generated_files)
    args.depfile.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=args.depfile.parent, prefix='.css-deps-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(escape(args.dep_target)+': '+' '.join(
                escape(os.path.relpath(path)) for path in sorted(dependencies))+'\n')
        os.replace(temporary, args.depfile)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    # Dynamic generated files are inputs too. Finish the directory only after
    # copying them so the action settles on the following Ninja invocation.
    os.utime(output, ns=(started_ns, started_ns))


if __name__ == '__main__':
    main()
