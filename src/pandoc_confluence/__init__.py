import argparse
import os
import io
import json
import shutil
import subprocess
import sys
from tempfile import mkdtemp, TemporaryDirectory
from textwrap import dedent
import urllib.parse

from pandocfilters import applyJSONFilters, Str, Image, Para, RawInline
import requests


class ConfluenceServer:

    def __init__(self, auth, url):
        self._auth = tuple(auth)
        self.url = url

    def attachment(self, content_id, attachment_name):
        response = requests.get(
            f'{self.url}/rest/api/content/{content_id}/child/attachment',
            auth=self._auth,
            params={'expand': 'children.attachment'}
        )

        attachments = response.json()['results']
        for attachment in attachments:
            if attachment['title'] == attachment_name:
                return attachment

    def page(self, title):
        response = requests.get(
            f'{self.url}/rest/api/content',
            params={'type': 'page', 'title': title},
            auth=self._auth,
        )

        response_json = response.json()
        if len(response_json['results']) != 1:
            print('Failed to find a unique page')
            exit(1)

        content_info = response_json['results'][0]
        response = requests.get(
            content_info['_links']['self'],
            params={'expand': 'body.editor'},
            auth=self._auth,
        )
        content_body = response.json()['body']['editor']['value']
        url = content_info["_links"]["self"]
        return content_body, url

    def upload(self, title, html_body):
        response = requests.get(
            f'{self.url}/rest/api/content',
            params={'type': 'page', 'title': title},
            auth=self._auth,
        )

        response_json = response.json()
        if len(response_json['results']) != 1:
            print('Failed to find a unique page')
            exit(1)

        content_info = response_json['results'][0]
        response = requests.get(
            content_info['_links']['self'],
            params={'expand': 'body.editor,version'},
            auth=self._auth,
        )
        content_body = response.json()

        payload = json.dumps({
            'title': content_info['title'],
            'type': 'page',
            'version': {'number': content_body['version']['number'] + 1},
            'body': {
                'editor': {
                    'value': html_body,
                    'representation': 'editor'
                },
            },
        })
        response = requests.put(
            content_info['_links']['self'],
            headers={
               "Accept": "application/json",
               "Content-Type": "application/json"
            },
            data=payload,
            auth=self._auth,
        )


class ConfluenceHTMLSourceFilter:

    def __init__(self, server):
        self.server = server

    def __call__(self, key, value, format, meta):
        ret = None
        if key == 'Image':
            ret = self.img_as_attachment(key, value, format, meta)

        return ret

    def img_as_attachment(self, key, value, format, meta):
        img_id, classes, attrs = value[0]
        src, alt_text = value[2]
        parsed_url = urllib.parse.urlparse(src)
        if parsed_url.netloc.find('atlassian.net'):
            # Image is already an attachment on Confluence, fetch comment
            # for LaTeX source
            content_id, attachment_name = parsed_url.path.split('/')[-2:]
            attachment = self.server.attachment(content_id, attachment_name)
            latex_src = urllib.parse.unquote_plus(attachment['metadata']['comment'])
            return RawInline('tex', '$$' + latex_src + '$$')


class ConfluenceHTMLTargetFilter:

    def __init__(self, server):
        self.server = server
        self._eq_counter = 1

    def __call__(self, key, value, format, meta):
        self._eq_counter = 1
        ret = None
        if key == 'Image':
            ret = self.embed_math(key, value, format, meta)

        return ret

    def latex2png(self, latex_src, outfile, filetype='png'):
        tmpdir = mkdtemp()
        olddir = os.getcwd()
        os.chdir(tmpdir)
        f = open('tikz.tex', 'w')
        doc = dedent(
        """
        \\documentclass[preview]{standalone}
        \\usepackage{amsmath,amssymb}
        \\begin{document}
        \\begin{align*}
        """)
        doc += latex_src.strip()
        doc += dedent("""
        \\end{align*}
        \\end{document}
        """)
        f.write(doc)
        f.close()
        # FIXME doesn't catched failed compiles
        retcode = subprocess.call(["pdflatex", 'tikz.tex'], stdout=sys.stderr)
        if retcode != 0:
            print(doc, file=sys.stderr)
            raise Exception('Conversion failed')
        os.chdir(olddir)
        if filetype == 'pdf':
            shutil.copyfile(tmpdir + '/tikz.pdf', outfile + '.pdf')
            shutil.copyfile(tmpdir + '/tikz.tex', outfile + '.tex')
            path = outfile + '.pdf'
        else:
            call(["convert", "-density", "300", '-quality', '85',
                  tmpdir + '/tikz.pdf', outfile + '.' + filetype],
                 stdout=sys.stderr)
            path = outfile + '.' + filetype
        shutil.rmtree(tmpdir)
        return path

    def embed_math(self, key, value, format, meta):
        """TODO: Docstring for embed_math.

        :key: TODO
        :value: TODO
        :format: TODO
        :meta: TODO
        :returns: TODO

        """
        dry_run = True
        page_url = meta['url']['c'][0]['c']
        parsed_url = urllib.parse.urlparse(page_url)
        content_id, _ = parsed_url.path.split('/')[-2:]

        # Still don't know how to handle InlineMath
        if key == 'Math' and value[0]['t'] == 'DisplayMath':
            classes = []
            attrs = []
            inline_elements = []
            latex_eq = value[1].strip()
            caption = ''
            eq_label = f'eq:{self._eq_counter}'
            quoted_src = urllib.parse.quote_plus(latex_eq)
            attrs.append(('latex', quoted_src))

            image_path = self.latex2png(
                latex_eq,
                f'images/{eq_label.replace(":", "_")}'
            )
            confluence_path = f'{self.server.url}/download/attachments/{content_id}/{eq_label.replace(":", "_")}.png'
            if not dry_run:
                # FIXME should find a way to avoid uploading if nothing as
                # changed or clean up old versions.
                with open(f'images/{eq_label.replace(":", "_")}.png', 'rb') as fin:
                    img_data = fin.read()

                # FIXME this should just be a function on self.server
                response = requests.put(
                    f'{self.server.url}/rest/api/content/{content_id}/child/attachment',
                    auth=self.server._auth,
                    headers={'X-Atlassian-Token': 'nocheck'},
                    files={
                        'comment': quoted_src,
                        'file': (f'{eq_label.replace(":", "_")}.png', img_data),
                    }
                )
                response.raise_for_status()

            self._eq_counter += 1
            return Image([eq_label, classes, attrs],
                         inline_elements,
                         [confluence_path, caption])


def toJSONFilters(actions, input=sys.stdin.buffer, output=sys.stdout):
    """Generate a JSON-to-JSON filter from stdin to stdout
    The filter:
    * reads a JSON-formatted pandoc document from stdin
    * transforms it by walking the tree and performing the actions
    * returns a new JSON-formatted pandoc document to stdout
    The argument `actions` is a list of functions of the form
    `action(key, value, format, meta)`, as described in more
    detail under `walk`.
    This function calls `applyJSONFilters`, with the `format`
    argument provided by the first command-line argument,
    if present.  (Pandoc sets this by default when calling
    filters.)
    """
    # Modified from pandocfilters to work with IOBytes
    source = input.read()
    format = ""

    output.write(applyJSONFilters(actions, source, format))


def parse_config(path):
    path = os.path.expanduser(path)
    with open(path, 'r') as f:
        config = json.load(f)
    return config['auth'], config['url']


def do_download(args):
    auth, url = parse_config(args.config_file)
    server = ConfluenceServer(auth, url)

    page_data, url = server.page(args.title)
    pandoc_process = subprocess.run(
        ['pandoc', '-t', 'json', '-f', 'html'],
        stdout=subprocess.PIPE,
        input=page_data.encode('utf-8')
    )
    pandoc_process.check_returncode()

    confluence_filter = ConfluenceHTMLSourceFilter(server)
    out_path = os.path.abspath(os.path.expanduser(args.output))
    filtered_io = io.StringIO()
    toJSONFilters(
        [confluence_filter],
        input=io.BytesIO(pandoc_process.stdout),
        output=filtered_io,
    )

    filtered_io.seek(0)
    pandoc_process = subprocess.run(
        ['pandoc', '-f', 'json', '-o', f'{out_path}',
         '-M', f'title:{args.title}', '-M', f'url:{url}',
         '-s'],
        input=filtered_io.read().encode('utf-8'),
    )
    pandoc_process.check_returncode()


def do_upload(args):
    # Looks at filter.py and push.py
    auth, url = parse_config(args.config_file)
    server = ConfluenceServer(auth, url)

    confluence_filter = ConfluenceHTMLTargetFilter(server)
    in_path = os.path.abspath(os.path.expanduser(args.input))
    pandoc_process = subprocess.run(
        ['pandoc', '-t', 'json', in_path],
        stdout=subprocess.PIPE,
    )
    pandoc_process.check_returncode()

    filtered_io = io.StringIO()
    with open(in_path, 'rb') as fin:
        toJSONFilters(
            [confluence_filter],
            input=io.BytesIO(pandoc_process.stdout),
            output=filtered_io,
        )

    filtered_io.seek(0)
    pandoc_process = subprocess.run(
        ['pandoc', '-t', 'html', '-f', 'json'],
        stdout=subprocess.PIPE,
        input=filtered_io.read().encode('utf-8')
    )
    pandoc_process.check_returncode()
    html_body = pandoc_process.stdout.decode('utf-8')
    server.upload(args.title, html_body)


def add_download_arguments(parser):
    parser.add_argument(
        'title',
        help='Title of Confluence page to download',
    )
    parser.add_argument(
        'output',
        help='File to output page to'
    )
    parser.set_defaults(func=do_download)
    return parser


def add_upload_arguments(parser):
    parser.add_argument(
        'input',
        help='File to upload'
    )
    parser.add_argument(
        'title',
        help='Title of Confluence page to upload',
    )
    parser.set_defaults(func=do_upload)
    return parser


def add_arguments(parser):
    parser.add_argument(
        '--config-file',
        default='~/.config/pandoc-confluence.json',
    )
    subparsers = parser.add_subparsers()
    # Workaround for Python <3.7 which is missing the required option on
    # add_subparsers. https://stackoverflow.com/a/18283730
    subparsers.required = True
    subparsers.dest = 'command'

    download_parser = subparsers.add_parser('download', help='Download Confluence page')
    add_download_arguments(download_parser)

    upload_parser = subparsers.add_parser('upload', help='Upload file to Confluence')
    add_upload_arguments(upload_parser)

    return parser


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser = add_arguments(parser)
    args = parser.parse_args(argv)
    args.func(args)
