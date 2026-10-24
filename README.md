# juntagrico-pulse


[![juntagrico-ci](https://github.com/juntagrico/juntagrico-pulse/actions/workflows/juntagrico-ci.yml/badge.svg?branch=main&event=push)](https://github.com/juntagrico/juntagrico-pulse/actions/workflows/juntagrico-ci.yml)
[![Maintainability](https://qlty.sh/gh/juntagrico/projects/juntagrico-pulse/maintainability.svg)](https://qlty.sh/gh/juntagrico/projects/juntagrico-pulse)
[![Code Coverage](https://qlty.sh/gh/juntagrico/projects/juntagrico-pulse/coverage.svg)](https://qlty.sh/gh/juntagrico/projects/juntagrico-pulse)
[![image](https://img.shields.io/github/last-commit/juntagrico/juntagrico-pulse.svg)](https://github.com/juntagrico/juntagrico-pulse)
[![image](https://img.shields.io/github/commit-activity/y/juntagrico/juntagrico-pulse)](https://github.com/juntagrico/juntagrico-pulse)

This is an extension for juntagrico. You can find more information about juntagrico here
(https://github.com/juntagrico/juntagrico).

It provides the following features for juntagrico:
* poll all participants on specific questions
* reporting of poll results

## Installation

Install juntagrico-pulse via pip
`pip install juntagrico-pulse`

or add it to your main django projects `requirements.txt`:
`juntagrico-pulse`

In your `settings.py`:

add `juntagrico_pulse` somewhere **above** `juntagrico` in `INSTALLED_APPS`:
```python
INSTALLED_APPS = (
    'juntagrico_pulse',
    'juntagrico',
    ...
```


And add this middleware after the `AuthenticationMiddleware`:
```python
INSTALLED_APPS = (
    ...
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'juntagrico_pulse.middleware.RequireSurveyCompletionMiddleware',
    ...
```


In your urls.py you also need to add the new pattern:
```python
urlpatterns = [
    ...
    path('',include('juntagrico_pulse.urls')),
]
```

As the app introduces its own database tables, you need to apply migrations after installing.
Execute `python manage.py migrate` in your main django project.

## Configuration

Set these in your `settings.py` to modify `juntagrico-pulse`

### TBD

  TBD