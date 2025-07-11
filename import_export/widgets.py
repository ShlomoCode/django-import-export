import json
import logging
import numbers
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from warnings import warn

import django
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.utils.dateparse import parse_duration
from django.utils.encoding import force_str, smart_str
from django.utils.formats import number_format, sanitize_separators
from django.utils.translation import gettext_lazy as _

from import_export.exceptions import WidgetError

logger = logging.getLogger(__name__)


def format_datetime(value, datetime_format):
    # handle correct formatting of dates
    # see https://code.djangoproject.com/ticket/32738
    format_ = django.utils.formats.sanitize_strftime_format(datetime_format)
    return value.strftime(format_)


class _ParseDateTimeMixin:
    """Internal Mixin for shared logic with date and datetime conversions."""

    def __init__(
        self,
        format=None,
        input_formats=None,
        default_format="%Y-%m-%d",
        coerce_to_string=True,
    ):
        super().__init__(coerce_to_string=coerce_to_string)
        self.formats = (format,) if format else (input_formats or (default_format,))

    def _parse_value(self, value, value_type):
        """Attempt to parse the value using the provided formats.
        Raise ValueError if parsing fails."""
        if not value:
            return None
        if isinstance(value, value_type):
            return value

        for format_ in self.formats:
            try:
                parsed_date = datetime.strptime(value, format_)
                if value_type is date:
                    return parsed_date.date()
                if value_type is time:
                    return parsed_date.time()
                return parsed_date
            except (ValueError, TypeError) as e:
                logger.debug(str(e))
        raise ValueError("Value could not be parsed using defined formats.")


class Widget:
    """
    A Widget handles converting between import and export representations.
    """

    def __init__(self, coerce_to_string=True):
        """
        :param coerce_to_string: If True, :meth:`~import_export.widgets.Widget.render`
          will return a string representation of the value, otherwise the value is
          returned.
        """
        self.coerce_to_string = coerce_to_string

    def clean(self, value, row=None, **kwargs):
        """
        Returns an appropriate python object for an imported value.
        For example, a date string will be converted to a python datetime instance.

        :param value: The value to be converted to a native type.
        :param row: A dict containing row key/value pairs.
        :param **kwargs: Optional kwargs.
        """
        return value

    def render(self, value, obj=None, **kwargs):
        """
        Returns an export representation of a python value.

        :param value: The python value to be rendered.
        :param obj: The model instance from which the value is taken.
          This parameter is deprecated and will be removed in a future release.

        :return: By default, this value will be a string, with ``None`` values returned
          as empty strings.
        """
        return force_str(value) if value is not None else ""

    def _obj_deprecation_warning(self, obj):
        if obj is not None:
            warn(
                "The 'obj' parameter is deprecated and will be removed "
                "in a future release",
                DeprecationWarning,
                stacklevel=2,
            )


class NumberWidget(Widget):
    """
    Widget for converting numeric fields.
    """

    def is_empty(self, value):
        if isinstance(value, str):
            value = value.strip()
        # 0 is not empty
        return value is None or value == ""

    def render(self, value, obj=None, **kwargs):
        self._obj_deprecation_warning(obj)
        if self.coerce_to_string and not kwargs.get("force_native_type"):
            return (
                ""
                if value is None or not isinstance(value, numbers.Number)
                else "" + number_format(value)
            )
        return value


class FloatWidget(NumberWidget):
    """
    Widget for converting float fields.
    """

    def clean(self, value, row=None, **kwargs):
        if self.is_empty(value):
            return None
        return float(sanitize_separators(value))


class IntegerWidget(NumberWidget):
    """
    Widget for converting integer fields.
    """

    def clean(self, value, row=None, **kwargs):
        if self.is_empty(value):
            return None
        return int(Decimal(sanitize_separators(value)))


class DecimalWidget(NumberWidget):
    """
    Widget for converting decimal fields.
    """

    def clean(self, value, row=None, **kwargs):
        if self.is_empty(value):
            return None
        return Decimal(force_str(sanitize_separators(value)))


class CharWidget(Widget):
    """
    Widget for converting text fields.

    :param allow_blank:  If True, then :meth:`~import_export.widgets.Widget.clean`
      will return null values as empty strings, otherwise as ``None``.
    """

    def __init__(self, coerce_to_string=True, allow_blank=True):
        """ """
        self.allow_blank = allow_blank
        super().__init__(coerce_to_string)

    def clean(self, value, row=None, **kwargs):
        val = super().clean(value, row, **kwargs)
        if val is None:
            return "" if self.allow_blank is True else None
        return force_str(val)

    def render(self, value, obj=None, **kwargs):
        self._obj_deprecation_warning(obj)
        if self.coerce_to_string:
            return "" if value is None else force_str(value)
        return value


class BooleanWidget(Widget):
    """
    Widget for converting boolean fields.

    The widget assumes that ``True``, ``False``, and ``None`` are all valid
    values, as to match Django's `BooleanField
    <https://docs.djangoproject.com/en/dev/ref/models/fields/#booleanfield>`_.
    That said, whether the database/Django will actually accept NULL values
    will depend on if you have set ``null=True`` on that Django field.

    While the BooleanWidget is set up to accept as input common variations of
    "True" and "False" (and "None"), you may need to munge less common values
    to ``True``/``False``/``None``. Probably the easiest way to do this is to
    override the :func:`~import_export.resources.Resource.before_import_row`
    function of your Resource class. A short example::

        from import_export import fields, resources, widgets

        class BooleanExample(resources.ModelResource):
            warn = fields.Field(widget=widgets.BooleanWidget())

            def before_import_row(self, row, **kwargs):
                if "warn" in row.keys():
                    # munge "warn" to "True"
                    if row["warn"] in ["warn", "WARN"]:
                        row["warn"] = True

                return super().before_import_row(row, **kwargs)
    """

    TRUE_VALUES = ["1", 1, True, "true", "TRUE", "True"]
    FALSE_VALUES = ["0", 0, False, "false", "FALSE", "False"]
    NULL_VALUES = ["", None, "null", "NULL", "none", "NONE", "None"]

    def __init__(self, coerce_to_string=True):
        """ """
        super().__init__(coerce_to_string)

    def clean(self, value, row=None, **kwargs):
        if value in self.NULL_VALUES:
            return None
        return True if value in self.TRUE_VALUES else False

    def render(self, value, obj=None, **kwargs):
        """
        :return: ``True`` is represented as ``1``, ``False`` as ``0``, and
          ``None``/NULL as an empty string.

          If ``coerce_to_string`` is ``False``, the python Boolean type is
          returned (may be ``None``).
        """
        self._obj_deprecation_warning(obj)
        if self.coerce_to_string and not kwargs.get("force_native_type"):
            if value in self.NULL_VALUES or not type(value) is bool:
                return ""
            return self.TRUE_VALUES[0] if value else self.FALSE_VALUES[0]
        return value


class DateWidget(_ParseDateTimeMixin, Widget):
    """
    Widget for converting date fields to Python date instances.

    Takes optional ``format`` parameter. If none is set, either
    ``settings.DATE_INPUT_FORMATS`` or ``"%Y-%m-%d"`` is used.
    """

    def __init__(self, format=None, coerce_to_string=True):
        super().__init__(
            format, settings.DATE_INPUT_FORMATS, "%Y-%m-%d", coerce_to_string
        )

    def clean(self, value, row=None, **kwargs):
        """
        :returns: A python date instance.
        :raises: ValueError if the value cannot be parsed using defined formats.
        """
        return self._parse_value(value, date)

    def render(self, value, obj=None, **kwargs):
        self._obj_deprecation_warning(obj)
        if self.coerce_to_string is False or kwargs.get("force_native_type"):
            return value
        if not value or not isinstance(value, date):
            return ""
        return format_datetime(value, self.formats[0])


class DateTimeWidget(_ParseDateTimeMixin, Widget):
    """
    Widget for converting datetime fields to Python datetime instances.

    Takes optional ``format`` parameter. If none is set, either
    ``settings.DATETIME_INPUT_FORMATS`` or ``"%Y-%m-%d %H:%M:%S"`` is used.
    """

    def __init__(self, format=None, coerce_to_string=True):
        super().__init__(
            format,
            settings.DATETIME_INPUT_FORMATS,
            "%Y-%m-%d %H:%M:%S",
            coerce_to_string,
        )

    def clean(self, value, row=None, **kwargs):
        """
        :returns: A python datetime instance.
        :raises: ValueError if the value cannot be parsed using defined formats.
        """
        dt = self._parse_value(value, datetime)
        if dt is None:
            return None
        if settings.USE_TZ and timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt

    def render(self, value, obj=None, **kwargs):
        self._obj_deprecation_warning(obj)
        if not value or not isinstance(value, datetime):
            return ""
        if settings.USE_TZ:
            value = timezone.localtime(value)

        force_native_type = kwargs.get("force_native_type")
        if self.coerce_to_string is False or force_native_type:
            # binary formats such as xlsx must not have tz set
            return value.replace(tzinfo=None) if force_native_type else value

        return format_datetime(value, self.formats[0])


class TimeWidget(_ParseDateTimeMixin, Widget):
    """
    Widget for converting time fields.

    Takes optional ``format`` parameter. If none is set, either
    ``settings.DATETIME_INPUT_FORMATS`` or ``"%H:%M:%S"`` is used.
    """

    def __init__(self, format=None, coerce_to_string=True):
        super().__init__(
            format, settings.TIME_INPUT_FORMATS, "%H:%M:%S", coerce_to_string
        )

    def clean(self, value, row=None, **kwargs):
        """
        :returns: A python time instance.
        :raises: ValueError if the value cannot be parsed using defined formats.
        """
        return self._parse_value(value, time)

    def render(self, value, obj=None, **kwargs):
        self._obj_deprecation_warning(obj)
        if self.coerce_to_string is False or kwargs.get("force_native_type"):
            return value
        if not value or not isinstance(value, time):
            return ""
        return value.strftime(self.formats[0])


class DurationWidget(Widget):
    """
    Widget for converting time duration fields.
    """

    def clean(self, value, row=None, **kwargs):
        """
        :returns: A python duration instance.
        :raises: ValueError if the value cannot be parsed.
        """
        if not value:
            return None

        try:
            return parse_duration(value)
        except (ValueError, TypeError) as e:
            logger.debug(str(e))
            raise ValueError(_("Value could not be parsed."))

    def render(self, value, obj=None, **kwargs):
        self._obj_deprecation_warning(obj)
        if self.coerce_to_string is False or kwargs.get("force_native_type"):
            return value
        if value is None or not type(value) is timedelta:
            return ""
        return str(value)


class SimpleArrayWidget(Widget):
    """
    Widget for an Array field. Can be used for Postgres' Array field.

    :param separator: Defaults to ``','``
    """

    def __init__(self, separator=None, coerce_to_string=True):
        if separator is None:
            separator = ","
        self.separator = separator
        super().__init__(coerce_to_string=coerce_to_string)

    def clean(self, value, row=None, **kwargs):
        return value.split(self.separator) if value else []

    def render(self, value, obj=None, **kwargs):
        """
        :return: A string with values separated by ``separator``.
          If ``coerce_to_string`` is ``False``, the native array will be returned.
          If ``value`` is None, None will be returned if ``coerce_to_string``
            is ``False``, otherwise an empty string will be returned.
        """
        self._obj_deprecation_warning(obj)
        if value is None:
            return "" if self.coerce_to_string is True else None
        if not self.coerce_to_string:
            return value
        return self.separator.join(str(v) for v in value)


class JSONWidget(Widget):
    """
    Widget for a JSON object
    (especially required for jsonb fields in PostgreSQL database.)

    :param value: Defaults to JSON format.
    The widget covers two cases: Proper JSON string with double quotes, else it
    tries to use single quotes and then convert it to proper JSON.
    """

    def clean(self, value, row=None, **kwargs):
        val = super().clean(value)
        if val:
            try:
                return json.loads(val)
            except json.decoder.JSONDecodeError:
                return json.loads(val.replace("'", '"'))

    def render(self, value, obj=None, **kwargs):
        """
        :return: A JSON formatted string derived from ``value``.
          ``coerce_to_string`` has no effect on the return value.
        """
        self._obj_deprecation_warning(obj)
        if value:
            return json.dumps(value)
        return None


class ForeignKeyWidget(Widget):
    """
    Widget for a ``ForeignKey`` field which looks up a related model using
    either the PK or a user specified field that uniquely identifies the
    instance in both export and import.

    The lookup field defaults to using the primary key (``pk``) as lookup
    criterion but can be customized to use any field on the related model.

    Unlike specifying a related field in your resource like so…

    ::

        class Meta:
            fields = ('author__name',)

    …using a :class:`~import_export.widgets.ForeignKeyWidget` has the
    advantage that it can not only be used for exporting, but also importing
    data with foreign key relationships.

    Here's an example on how to use
    :class:`~import_export.widgets.ForeignKeyWidget` to lookup related objects
    using ``Author.name`` instead of ``Author.pk``::

        from import_export import fields, resources
        from import_export.widgets import ForeignKeyWidget

        class BookResource(resources.ModelResource):
            author = fields.Field(
                column_name='author',
                attribute='author',
                widget=ForeignKeyWidget(Author, 'name'))

            class Meta:
                fields = ('author',)

    :param model: The Model the ForeignKey refers to (required).
    :param field: A field on the related model used for looking up a particular
        object.
    :param use_natural_foreign_keys: Use natural key functions to identify
        related object, default to False
    """

    def __init__(
        self,
        model,
        field="pk",
        use_natural_foreign_keys=False,
        key_is_id=False,
        **kwargs,
    ):
        self.model = model
        self.field = field
        self.key_is_id = key_is_id
        self.use_natural_foreign_keys = use_natural_foreign_keys
        if use_natural_foreign_keys is True and key_is_id is True:
            raise WidgetError(
                _("use_natural_foreign_keys and key_is_id cannot both be True")
            )
        super().__init__(**kwargs)

    def get_queryset(self, value, row, *args, **kwargs):
        """
        Returns a queryset of all objects for this Model.

        Overwrite this method if you want to limit the pool of objects from
        which the related object is retrieved.

        :param value: The field's value in the dataset.
        :param row: The dataset's current row.
        :param \\*args:
            Optional args.
        :param \\**kwargs:
            Optional kwargs.

        As an example; if you'd like to have ForeignKeyWidget look up a Person
        by their pre- **and** lastname column, you could subclass the widget
        like so::

            class FullNameForeignKeyWidget(ForeignKeyWidget):
                def get_queryset(self, value, row, *args, **kwargs):
                    return self.model.objects.filter(
                        first_name__iexact=row["first_name"],
                        last_name__iexact=row["last_name"]
                    )
        """
        return self.model.objects.all()

    def clean(self, value, row=None, **kwargs):
        """
        :return: a single Foreign Key instance derived from the args.
          ``None`` can be returned if the value passed is a null value.

        :param value: The field's value in the dataset.
        :param row: The dataset's current row.
        :param \\**kwargs:
            Optional kwargs.
        :raises: ``ObjectDoesNotExist`` if no valid instance can be found.
        """
        val = super().clean(value)
        if val:
            if self.use_natural_foreign_keys:
                # natural keys will always be a tuple, which ends up as a json list.
                value = json.loads(value)
                return self.model.objects.get_by_natural_key(*value)
            else:
                lookup_kwargs = self.get_lookup_kwargs(value, row, **kwargs)
                obj = self.get_queryset(value, row, **kwargs).get(**lookup_kwargs)
                if self.key_is_id:
                    return obj.pk
                return obj
        else:
            return None

    def get_lookup_kwargs(self, value, row, **kwargs):
        """
        :return: the key value pairs used to identify a model instance.
          Override this to customize instance lookup.

        :param value: The field's value in the dataset.
        :param row: The dataset's current row.
        :param \\**kwargs:
            Optional kwargs.
        """
        return {self.field: value}

    def render(self, value, obj=None, **kwargs):
        """
        :return: A string representation of the related value.
          If ``use_natural_foreign_keys``, the value's natural key is returned.
          ``coerce_to_string`` has no effect on the return value.
        """
        self._obj_deprecation_warning(obj)

        if self.key_is_id:
            return value or ""

        if value is None:
            return ""

        attrs = self.field.split("__")
        for attr in attrs:
            try:
                if self.use_natural_foreign_keys:
                    # inbound natural keys must be a json list.
                    return json.dumps(value.natural_key())
                else:
                    value = getattr(value, attr, None)
            except (ValueError, ObjectDoesNotExist):
                # needs to have a primary key value before a many-to-many
                # relationship can be used.
                return None
            if value is None:
                return None

        return value


class ManyToManyWidget(Widget):
    """
    Widget that converts between representations of a ManyToMany relationships
    as a list and an actual ManyToMany field.

    :param model: The model the ManyToMany field refers to (required).
    :param separator: Defaults to ``','``.
    :param field: A field on the related model. Default is ``pk``.
    """

    def __init__(self, model, separator=None, field=None, **kwargs):
        if separator is None:
            separator = ","
        if field is None:
            field = "pk"
        self.model = model
        self.separator = separator
        self.field = field
        super().__init__(**kwargs)

    def clean(self, value, row=None, **kwargs):
        if not value:
            return self.model.objects.none()
        if isinstance(value, (float, int)):
            ids = [int(value)]
        else:
            ids = value.split(self.separator)
            ids = filter(None, [i.strip() for i in ids])
        return self.model.objects.filter(**{"%s__in" % self.field: ids})

    def render(self, value, obj=None, **kwargs):
        """
        :return: A string with values separated by ``separator``.
          ``None`` values are returned as empty strings.
          ``coerce_to_string`` has no effect on the return value.
        """
        self._obj_deprecation_warning(obj)
        if value is not None:
            ids = [smart_str(getattr(obj, self.field)) for obj in value.all()]
            return self.separator.join(ids)
        return ""
