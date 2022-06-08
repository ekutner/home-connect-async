from distutils.core import setup

setup(
    name = 'home-connect-async',
    packages = ['home_connect_async'],
    version = '0.7.0-b2',
    license='MIT',
    description = 'Async SDK for BSH Home Connect API',
    author = 'Eran Kutner',
    author_email = 'eran@kutner.org',
    url = 'https://github.com/ekutner/home-connect-async',
    keywords = ['HomeConnect', 'Home Connect', 'BSH', 'Async', 'SDK'],
    install_requires=[
        'aiohttp',
        'aiohttp-sse-client>=0.2.1',
        'dataclasses-json>=0.5.6',
        'oauth2-client>=1.2.1',
        'cchardet'
    ],
    classifiers=[
        'Development Status :: 4 - Beta',      # Chose either "3 - Alpha", "4 - Beta" or "5 - Production/Stable" as the current state of your package
        'Intended Audience :: Developers',      # Define that your audience are developers
        'Topic :: Software Development :: Build Tools',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
)