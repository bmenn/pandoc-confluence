from setuptools import setup, find_packages

VERSION = '0.1.0'

install_requires = [
    "pandocfilters",
    "requests"
]
test_requires = [
    'pytest',
]
test_utils = [
    'pytest-coverage',
    'pytest-pylint',
    'black',
]

setup(
    name="pandoc-confluence",
    version=VERSION,
    packages=find_packages(where="src", exclude=("tests", )),
    package_dir={"": "src"},
    install_requires=install_requires,
    test_requires=test_requires,
    extras_require={
        'tests': test_requires + test_utils,
        'test-utils': test_utils,
    },
    entry_points={
        'console_scripts': [
            'confluence-markdown=pandoc_confluence:main'
        ],
    }
)
