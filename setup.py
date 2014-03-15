from setuptools import setup, find_packages

setup(
    name="funcserver",
    version='0.1',
    description="Functionality Server",
    keywords='funcserver',
    author='Prashanth Ellina',
    author_email="Use the github issues",
    url="https://github.com/prashanthellina/funcserver",
    license='MIT License',
    install_requires=[
        'tornado',
    ],
    package_dir={'funcserver': 'funcserver'},
    packages=find_packages('.'),
    include_package_data=True
)
