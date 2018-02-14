# ------------------------------------------------------------------------------
# Joyous events models
# ------------------------------------------------------------------------------
import datetime as dt
import calendar
from collections import namedtuple
from operator import attrgetter
from django.conf import settings
from django.db import models
from wagtail.wagtailcore.models import Page
from wagtail.wagtailcore.fields import RichTextField
from wagtail.wagtailadmin.edit_handlers import FieldPanel, MultiFieldPanel, \
    PageChooserPanel
from wagtail.wagtailimages.edit_handlers import ImageChooserPanel
from wagtail.wagtailimages import get_image_model_string
from wagtail.wagtailsearch import index
from wagtail.wagtailadmin.forms import WagtailAdminPageForm
from django.utils.html import format_html
from ..holidays.parser import parseHolidays
from ..utils.telltime import getDatetime, datetimeFrom, datetimeTo
from ..utils.telltime import timeFormat, dateFormat
# from ..utils.ical import export_event
from ..recurrence import RecurrenceField
from ..recurrence import ExceptionDatePanel
from ..widgets import TimeInput
from .groups import get_group_model_string, get_group_model

# ------------------------------------------------------------------------------
# Event Pages
# ------------------------------------------------------------------------------
class EventCategory(models.Model):
    class Meta:
        ordering = ["name"]
        verbose_name = "Event Category"
        verbose_name_plural = "Event Categories"

    code = models.CharField(max_length=4, unique=True)
    name = models.CharField(max_length=80)

    def __str__(self):
        return self.name

class EventBase(models.Model):
    class Meta:
        abstract = True

    category = models.ForeignKey(EventCategory,
                                 related_name="+",
                                 verbose_name="Category",
                                 on_delete=models.SET_NULL,
                                 blank=True, null=True)
    time_from = models.TimeField("Start time", null=True, blank=True)
    time_to = models.TimeField("End time", null=True, blank=True)
    image = models.ForeignKey(get_image_model_string(),
                              null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='+')
    group_page  = models.ForeignKey(get_group_model_string(),
                                    null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="%(class)s_events",
                                    related_query_name="%(class)s_event")
    location = models.CharField(max_length=255, blank=True)
    details  = RichTextField(blank=True)
    website = models.URLField(blank=True)

    search_fields = Page.search_fields + [
        index.SearchField('location'),
        index.SearchField('details'),
    ]

    # adding the event as a child of a group automatically assigns the event to
    # that group.
    @property
    def group(self):
        retval = None
        parent = self.get_parent()
        Group = get_group_model()
        if issubclass(parent.specific_class, Group):
            retval = parent.specific
        if retval is None:
            retval = self.group_page
        return retval

    @property
    def _upcoming_datetime_from(self):
        fromDt = self._getFromDt()
        return fromDt if fromDt >= dt.datetime.now() else None

    @property
    def _past_datetime_from(self):
        fromDt = self._getFromDt()
        return fromDt if fromDt < dt.datetime.now() else None

    @property
    def status(self):
        return None

    @property
    def status_text(self):
        status = self.status
        if status == "finished":
            return "This event has finished."
        elif status == "started":
            return "This event has started."
        else:
            return ""

    def _getFromDt(self):
        raise NotImplementedError()

ThisEvent = namedtuple("ThisEvent", "title page")

class EventsOnDay(namedtuple("EODBase", "date days_events continuing_events")):
    holidays = parseHolidays(getattr(settings, "JOYOUS_HOLIDAYS", ""))

    @property
    def weekday(self):
        return calendar.day_abbr[self.date.weekday()].lower()

    @property
    def holiday(self):
        return self.holidays.get(self.date)

def _getEventsByDay(date_from, date_to, eventsByDaySrcs):
    eventsByDay = []
    day = date_from
    for srcs in zip(*eventsByDaySrcs):
        days_events       = []
        continuing_events = []
        for src in srcs:
            days_events += src.days_events
            continuing_events += src.continuing_events
        def sortByTime(thisEvent):
            time_from = thisEvent.page.time_from
            if time_from is None:
                time_from = dt.time.max
            return time_from
        days_events.sort(key=sortByTime)
        eventsByDay.append(EventsOnDay(day, days_events, continuing_events))
        day += dt.timedelta(days=1)
    return eventsByDay

def getAllEventsByDay(date_from, date_to):
    simpleEvents    = SimpleEventPage.getEventsByDay(date_from, date_to)
    multidayEvents  = MultidayEventPage.getEventsByDay(date_from, date_to)
    recurringEvents = RecurringEventPage.getEventsByDay(date_from, date_to)
    postponedEvents = PostponementPage.getEventsByDay(date_from, date_to)
    allEvents = _getEventsByDay(date_from, date_to, (simpleEvents,
                                                     multidayEvents,
                                                     recurringEvents,
                                                     postponedEvents))
    return allEvents

def _getUpcomingEvents(simpleEventsQry=None,
                       multidayEventsQry=None,
                       recurringEventsQry=None,
                       postponedEventsQry=None):
    now = dt.datetime.now()
    today = now.date()
    events = []
    if simpleEventsQry is not None:
        for event in simpleEventsQry.live().filter(date__gte=today):
            if event._upcoming_datetime_from:
                events.append(ThisEvent(event.title, event))
    if multidayEventsQry is not None:
        for event in multidayEventsQry.live().filter(date_from__gte=today):
            if event._upcoming_datetime_from:
                events.append(ThisEvent(event.title, event))
    if recurringEventsQry is not None:
        for event in recurringEventsQry.live():
            if event._upcoming_datetime_from:
                events.append(ThisEvent(event.title, event))
    if postponedEventsQry is not None:
        for event in postponedEventsQry.live().filter(date__gte=today):
            if event._upcoming_datetime_from >= now:
                events.append(ThisEvent(event.postponement_title, event))
    return events

def getAllUpcomingEvents():
    events =  _getUpcomingEvents(SimpleEventPage.objects,
                                 MultidayEventPage.objects,
                                 RecurringEventPage.objects,
                                 PostponementPage.objects)
    events.sort(key=attrgetter('page._upcoming_datetime_from'))
    return events

def getGroupUpcomingEvents(group):
    events = _getUpcomingEvents(SimpleEventPage.objects.child_of(group),
                                MultidayEventPage.objects.child_of(group),
                                RecurringEventPage.objects.child_of(group),
                                PostponementPage.objects.child_of(group))
    events += _getUpcomingEvents(group.simpleeventpage_events,
                                 group.multidayeventpage_events,
                                 group.recurringeventpage_events,
                                 group.postponementpage_events)
    events.sort(key=attrgetter('page._upcoming_datetime_from'))
    return events

def _getPastEvents(simpleEventsQry=None,
                   multidayEventsQry=None,
                   recurringEventsQry=None,
                   postponedEventsQry=None):
    now = dt.datetime.now()
    today = now.date()
    events = []
    if simpleEventsQry is not None:
        for event in simpleEventsQry.live().filter(date__lte=today):
            if event._past_datetime_from:
                events.append(ThisEvent(event.title, event))
    if multidayEventsQry is not None:
        for event in multidayEventsQry.live().filter(date_from__lte=today):
            if event._past_datetime_from:
                events.append(ThisEvent(event.title, event))
    if recurringEventsQry is not None:
        for event in recurringEventsQry.live():
            if event._past_datetime_from:
                events.append(ThisEvent(event.title, event))
    if postponedEventsQry is not None:
        for event in postponedEventsQry.live().filter(date__lte=today):
            if event._past_datetime_from:
                events.append(ThisEvent(event.postponement_title, event))
    events.sort(key=attrgetter('page._past_datetime_from'), reverse=True)
    return events

def getAllPastEvents():
    return _getPastEvents(SimpleEventPage.objects,
                          MultidayEventPage.objects,
                          RecurringEventPage.objects,
                          PostponementPage.objects)

# ------------------------------------------------------------------------------
class SimpleEventPage(Page, EventBase):
    class Meta:
        verbose_name = "One-Off Event Page"

    parent_page_types = ["joyous.CalendarPage",
                         get_group_model_string()]
    subpage_types = []

    date    = models.DateField("Date", default=dt.date.today)

    content_panels = Page.content_panels + [
        FieldPanel('category'),
        ImageChooserPanel('image'),
        FieldPanel('date'),
        FieldPanel('time_from', widget=TimeInput),
        FieldPanel('time_to', widget=TimeInput),
        FieldPanel('details', classname="full"),
        FieldPanel('location'),
        FieldPanel('website'),
    ]
    if getattr(settings, "JOYOUS_GROUP_SELECTABLE", False):
        content_panels.append(PageChooserPanel('group_page'))

    @classmethod
    def getEventsByDay(cls, date_from, date_to):
        ord_from =  date_from.toordinal()
        ord_to   =  date_to.toordinal()
        events = [EventsOnDay(dt.date.fromordinal(ord), [], [])
                  for ord in range(ord_from, ord_to+1)]
        pages = SimpleEventPage.objects.live()                              \
                               .filter(date__range=(date_from, date_to))
        for page in pages:
            day_num = page.date.toordinal() - ord_from
            events[day_num].days_events.append(ThisEvent(page.title, page))
        return events

    @property
    def status(self):
        now = dt.datetime.now()
        if datetimeTo(self.date, self.time_to) < now:
            return "finished"
        if self.time_from is not None:
            if dt.datetime.combine(self.date, self.time_from) < now:
                return "started"
        return None

    @property
    def when(self):
        return "{} {}".format(dateFormat(self.date),
                              timeFormat(self.time_from, self.time_to, "at "))

    @property
    def at(self):
        return timeFormat(self.time_from)

    def _getFromDt(self):
        return getDatetime(self.date, self.time_from, dt.time.max)

# ------------------------------------------------------------------------------
class MultidayEventPage(Page, EventBase):
    class Meta:
        verbose_name = "Multiday Event Page"

    parent_page_types = ["joyous.CalendarPage",
                         get_group_model_string()]
    subpage_types = []

    date_from = models.DateField("Start date", default=dt.date.today)
    date_to = models.DateField("End date", default=dt.date.today)

    content_panels = Page.content_panels + [
        FieldPanel('category'),
        ImageChooserPanel('image'),
        FieldPanel('date_from'),
        FieldPanel('time_from', widget=TimeInput),
        FieldPanel('date_to'),
        FieldPanel('time_to', widget=TimeInput),
        FieldPanel('details', classname="full"),
        FieldPanel('location'),
        FieldPanel('website'),
    ]
    if getattr(settings, "JOYOUS_GROUP_SELECTABLE", False):
        content_panels.append(PageChooserPanel('group_page'))

    @classmethod
    def getEventsByDay(cls, date_from, date_to):
        events = []
        ord_from =  date_from.toordinal()
        ord_to   =  date_to.toordinal()
        days = [dt.date.fromordinal(ord) for ord in range(ord_from, ord_to+1)]
        pages = MultidayEventPage.objects.live()                       \
                                 .filter(date_to__gte   = date_from)   \
                                 .filter(date_from__lte = date_to)
        for day in days:
            days_events = []
            continuing_events = []
            for page in pages:
                if page.date_from == day:
                    days_events.append(ThisEvent(page.title, page))
                elif page.date_from < day <= page.date_to:
                    continuing_events.append(ThisEvent(page.title, page))
            events.append(EventsOnDay(day, days_events, continuing_events))
        return events

    @property
    def status(self):
        now = dt.datetime.now()
        if datetimeTo(self.date_to, self.time_to) < now:
            return "finished"
        if self.time_from is not None:
            if dt.datetime.combine(self.date_from, self.time_from) < now:
                return "started"
        return None

    @property
    def when(self):
        return "{} {} to {} {}".format(dateFormat(self.date_from),
                                       timeFormat(self.time_from),
                                       dateFormat(self.date_to),
                                       timeFormat(self.time_to))

    @property
    def at(self):
        return timeFormat(self.time_from)

    def _getFromDt(self):
        return getDatetime(self.date_from, self.time_from, dt.time.max)

# ------------------------------------------------------------------------------
class RecurringEventPage(Page, EventBase):
    class Meta:
        verbose_name = "Recurring Event Page"

    parent_page_types = ["joyous.CalendarPage",
                         get_group_model_string()]
    subpage_types = ['joyous.ExtraInfoPage',
                     'joyous.CancellationPage',
                     'joyous.PostponementPage']

    # FIXME So that Fred can't cancel Barney's event
    # owner_subpages_only = True

    repeat  = RecurrenceField()

    content_panels = Page.content_panels + [
        FieldPanel('category'),
        ImageChooserPanel('image'),
        FieldPanel('repeat'),
        FieldPanel('time_from', widget=TimeInput),
        FieldPanel('time_to', widget=TimeInput),
        FieldPanel('details', classname="full"),
        FieldPanel('location'),
        FieldPanel('website'),
        ]
    if getattr(settings, "JOYOUS_GROUP_SELECTABLE", False):
        content_panels.append(PageChooserPanel('group_page'))

    @property
    def next_date(self):
        """
        Date when this event is next scheduled to occur
        (Does not include postponements, but does exclude cancellations)
        """
        nextDt = self.__after(dt.datetime.now())
        if nextDt:
            return nextDt.date()
        else:
            return None

    @property
    def _upcoming_datetime_from(self):
        nextDate = self.next_date
        if nextDate:
            return getDatetime(nextDate, self.time_from, dt.time.max)
        else:
            return None

    @property
    def prev_date(self):
        """
        Date when this event last occurred
        (Does not include postponements, but does exclude cancellations)
        """
        prevDt = self.__before(dt.datetime.now())
        if prevDt:
            return prevDt.date()
        else:
            return None

    @property
    def _past_datetime_from(self):
        prevDate = self.prev_date
        if prevDate:
            return getDatetime(prevDate, self.time_from, dt.time.max)
        else:
            return None

    @property
    def next_on(self):
        """
        Formatted date/time of when this event (including any postponements)
        will next be on
        """
        retval = None
        nextDt, event = self.__afterOrPostponedTo(dt.datetime.now())
        if nextDt:
            retval = "{} {}".format(dateFormat(nextDt.date()),
                                    timeFormat(event.time_from, prefix="at "))
            if event is not self:
                retval = format_html('<a class="inline-link" href="{}">{}</a>', event.url, retval)
        return retval

    @property
    def status(self):
        now = dt.datetime.now()
        if self.repeat.until:
            if datetimeTo(self.repeat.until.date(), self.time_to) < now:
                return "finished"
        if self.time_from:
            todayStart = dt.datetime.combine(dt.date.today(), dt.time.min)
            eventStart = self.__afterOrPostponedTo(todayStart)[0]
            if eventStart is None:
                return "finished"
            eventFinish = datetimeTo(eventStart.date(), self.time_to)
            if eventStart < now < eventFinish:
                # If there are two occurences on the same day then we may miss
                # that one of them has started
                return "started"
            if (self.repeat.until and eventFinish < now and
                self.__afterOrPostponedTo(now)[0] is None):
                return "finished"
        return None

    @property
    def status_text(self):
        status = self.status
        if status == "finished":
            return "These events have finished."
        else:
            return super().status_text

    @property
    def when(self):
        return "{} {}".format(self.repeat,
                              timeFormat(self.time_from, self.time_to, "at "))

    @property
    def at(self):
        return timeFormat(self.time_from)

    @classmethod
    def getEventsByDay(cls, date_from, date_to):
        ord_from =  date_from.toordinal()
        ord_to   =  date_to.toordinal()
        dt_from  = dt.datetime.combine(date_from, dt.time.min)
        dt_to    = dt.datetime.combine(date_to,   dt.time.max)
        events = [EventsOnDay(dt.date.fromordinal(ord), [], [])
                  for ord in range(ord_from, ord_to+1)]
        pages = RecurringEventPage.objects.live()
        for page in pages:
            exceptions = page.__getExceptions(date_from, date_to)

            for occurence in page.repeat.between(dt_from, dt_to, True):
                day_num = occurence.toordinal() - ord_from
                exception = exceptions.get(occurence.date())
                if exception:
                    if exception.exception_title:
                        events[day_num].days_events.append(ThisEvent(exception.exception_title,
                                                                     exception))
                else:
                    events[day_num].days_events.append(ThisEvent(page.title, page))
        return events

    def __afterOrPostponedTo(self, fromDt):
        after = self.__after(fromDt)
        if after:
            # is there a postponed event before that?
            # nb: range is inclusive
            postponement = PostponementPage.objects.live().child_of(self)                       \
                                           .filter(date__range=(fromDt.date(), after.date()))   \
                                           .order_by('date', 'time_from').first()
            if postponement:
                postDt = datetimeFrom(postponement.date, postponement.time_from)
                if postDt < after:
                    return (postDt, postponement)
        else:
            # is there a postponed event then?
            postponement = PostponementPage.objects.live().child_of(self)                       \
                                           .filter(date__gte=fromDt.date())                     \
                                           .order_by('date', 'time_from').first()
            if postponement:
                postDt = datetimeFrom(postponement.date, postponement.time_from)
                return (postDt, postponement)
        return (after, self)

    def __after(self, fromDt):
        fromDate = fromDt.date()
        if self.time_from and self.time_from < fromDt.time():
            fromDate += dt.timedelta(days=1)
        fromStart = dt.datetime.combine(fromDate, dt.time.min)
        cancellations = {cancelled.except_date for cancelled in
                         CancellationPage.objects.live().child_of(self)
                                         .filter(except_date__gte=fromDate) }
        for occurence in self.repeat.xafter(fromStart, inc=True):
            if occurence.date() not in cancellations:
                return datetimeFrom(occurence.date(), self.time_from)
        return None

    def __before(self, fromDt):
        fromDate = fromDt.date()
        if self.time_from and self.time_from > fromDt.time():
            fromDate -= dt.timedelta(days=1)
        fromStart = dt.datetime.combine(fromDate, dt.time.min)
        cancellations = {cancelled.except_date for cancelled in
                         CancellationPage.objects.live().child_of(self)
                                         .filter(except_date__lte=fromDate) }
        last = None
        for occurence in self.repeat:
            if occurence >= fromStart:
                break
            if occurence.date() not in cancellations:
                last = occurence
        return last

    def __getExceptions(self, date_from, date_to):
        exceptions = {}
        for exception in ExtraInfoPage.objects.live().child_of(self)  \
                         .filter(except_date__range=(date_from, date_to)):
            exceptions[exception.except_date] = exception
        for exception in CancellationPage.objects.live().child_of(self)  \
                         .filter(except_date__range=(date_from, date_to)):
            exceptions[exception.except_date] = exception
        return exceptions

    # def serve(self, request):
    #     if "format" in request.GET:
    #         if request.GET['format'] == 'ical':
    #             # Export to ical format
    #             response = HttpResponse(export_event(self, 'ical'),
    #                                     content_type='text/calendar')
    #             response['Content-Disposition'] = \
    #                 'attachment; filename={}.ics'.format(self.slug)
    #             return response
    #         else:
    #             # Unrecognised format error
    #             return HttpResponse('Could not export event\n\n'
    #                                 'Unrecognised format: {}'.
    #                                     format(request.GET['format']),
    #                                 content_type='text/plain')
    #     else:
    #         # Display event page as usual
    #         return super().serve(request)

# ------------------------------------------------------------------------------
class EventExceptionPageForm(WagtailAdminPageForm):
    def clean(self):
        cleaned_data = super().clean()
        self._checkSlugAvailable(cleaned_data['except_date'],
                                 "exception", "an event exception")
        return cleaned_data

    def _checkSlugAvailable(self, exceptDate, slugName, description):
        slug = "{}-{}".format(exceptDate, slugName)
        if not Page._slug_is_available(slug, self.parent_page, self.instance):
            self.add_error('except_date',
                           'That date already has {}'.format(description))

    def save(self, commit=True):
        page = super().save(commit=False)
        page.title = "Exception for {}".format(dateFormat(page.except_date))
        page.slug = "{}-exception".format(page.except_date)
        if commit:
            page.save()
        return page

class EventExceptionBase(models.Model):
    class Meta:
        abstract = True

    # overrides is also the parent, but parent is not set until the
    # child is saved and added.  (NB: is published version of parent)
    overrides = models.ForeignKey('joyous.RecurringEventPage',
                                  null=True, blank=False,
                                  on_delete=models.SET_NULL,
                                  related_name='+')
    overrides.help_text = "The recurring event that we are updating."
    except_date = models.DateField('For Date')
    except_date.help_text = "For this date"

    @property
    def group(self):
        return self.overrides.group

    @property
    def overrides_repeat(self):
        return getattr(self.overrides, 'repeat', None)

    @property
    def exception_title(self):
        return None

    @property
    def status_text(self):
        return EventBase.status_text(self)

    @property
    def when(self):
        return "{} {}".format(dateFormat(self.except_date),
                              timeFormat(self.overrides.time_from,
                                         self.overrides.time_to, "at "))

    @property
    def at(self):
        return timeFormat(self.overrides.time_from)

# ------------------------------------------------------------------------------
class ExtraInfoPageForm(EventExceptionPageForm):
    slugName = "extra-info"

    def clean(self):
        cleaned_data = super().clean()
        self._checkSlugAvailable(cleaned_data['except_date'],
                                 self.slugName, "extra information")
        return cleaned_data

    def save(self, commit=True):
        page = super().save(commit=False)
        page.title = "Extra Information for {}".format(dateFormat(page.except_date))
        page.slug = "{}-{}".format(page.except_date, self.slugName)
        if commit:
            page.save()
        return page

class ExtraInfoPage(Page, EventExceptionBase):
    class Meta:
        verbose_name = "Extra Event Information"

    parent_page_types = ["joyous.RecurringEventPage"]
    subpage_types = []
    base_form_class = ExtraInfoPageForm

    extra_information = RichTextField(blank=False)
    extra_information.help_text = "Information just for this date"

    # Note title is not displayed
    content_panels = [
        PageChooserPanel('overrides'),
        ExceptionDatePanel('except_date'),
        FieldPanel('extra_information', classname="full"),
        ]
    promote_panels = []

    @property
    def time_from(self):
        return self.overrides.time_from

    @property
    def status(self):
        now = dt.datetime.now()
        if datetimeTo(self.except_date, self.overrides.time_to) < now:
            return "finished"
        if self.overrides.time_from is not None:
            if dt.datetime.combine(self.except_date, self.overrides.time_from) < now:
                return "started"
        return None

    @property
    def exception_title(self):
        return self.overrides.title

    def _getFromDt(self):
        return getDatetime(self.except_date, self.overrides.time_from, dt.time.max)

# ------------------------------------------------------------------------------
class CancellationPageForm(EventExceptionPageForm):
    slugName = "cancellation"

    def clean(self):
        cleaned_data = super().clean()
        self._checkSlugAvailable(cleaned_data['except_date'],
                                 self.slugName, "a cancellation")
        self._checkSlugAvailable(cleaned_data['except_date'],
                                 "postponement", "a postponement")
        return cleaned_data

    def save(self, commit=True):
        page = super().save(commit=False)
        page.title = "Cancellation for {}".format(dateFormat(page.except_date))
        page.slug = "{}-{}".format(page.except_date, self.slugName)
        if commit:
            page.save()
        return page

class CancellationPage(Page, EventExceptionBase):
    class Meta:
        verbose_name = "Cancellation"

    parent_page_types = ["joyous.RecurringEventPage"]
    subpage_types = []
    base_form_class = CancellationPageForm

    cancellation_title = models.CharField('Title', max_length=255, blank=True)
    cancellation_title.help_text = "Show in place of cancelled event "\
                                   "(Leave empty to show nothing)"
    cancellation_details = RichTextField('Details', blank=True)
    cancellation_details.help_text = "Why was the event cancelled?"

    # Note title is not displayed
    content_panels = [
        PageChooserPanel('overrides'),
        ExceptionDatePanel('except_date'),
        MultiFieldPanel([
            FieldPanel('cancellation_title', classname="full title"),
            FieldPanel('cancellation_details', classname="full")],
            heading="Cancellation"),
        ]
    promote_panels = []

    @property
    def time_from(self):
        return self.overrides.time_from

    @property
    def status(self):
        return "cancelled"

    @property
    def status_text(self):
        return "This event has been cancelled."

    @property
    def exception_title(self):
        return self.cancellation_title

    def _getFromDt(self):
        return getDatetime(self.except_date, self.overrides.time_from, dt.time.max)

# ------------------------------------------------------------------------------
class PostponementPageForm(EventExceptionPageForm):
    slugName = "postponement"

    def clean(self):
        cleaned_data = super().clean()
        self._checkSlugAvailable(cleaned_data['except_date'],
                                 self.slugName, "a postponement")
        self._checkSlugAvailable(cleaned_data['except_date'],
                                 "cancellation", "a cancellation")
        return cleaned_data

    def save(self, commit=True):
        page = super().save(commit=False)
        page.title = "Postponement for {}".format(dateFormat(page.except_date))
        page.slug = "{}-{}".format(page.except_date, self.slugName)
        if commit:
            page.save()
        return page

class PostponementPage(CancellationPage, EventBase):
    class Meta:
        verbose_name = "Postponement"

    parent_page_types = ["joyous.RecurringEventPage"]
    subpage_types = []
    base_form_class = PostponementPageForm

    postponement_title = models.CharField('Title', max_length=255)
    postponement_title.help_text = "The title for the postponed event"
    date    = models.DateField("Date")

    content_panels = [
        PageChooserPanel('overrides'),
        ExceptionDatePanel('except_date'),
        MultiFieldPanel([
            FieldPanel('cancellation_title', classname="full title"),
            FieldPanel('cancellation_details', classname="full")],
            heading="Cancellation"),
        MultiFieldPanel([
            FieldPanel('postponement_title', classname="full title"),
            ImageChooserPanel('image'),
            FieldPanel('date'),
            FieldPanel('time_from', widget=TimeInput),
            FieldPanel('time_to', widget=TimeInput),
            FieldPanel('details', classname="full"),
            FieldPanel('location'),
            FieldPanel('website')],
            heading="Postponed to"),
    ]
    promote_panels = []

    @classmethod
    def getEventsByDay(cls, date_from, date_to):
        ord_from =  date_from.toordinal()
        ord_to   =  date_to.toordinal()
        events = [EventsOnDay(dt.date.fromordinal(ord), [], [])
                  for ord in range(ord_from, ord_to+1)]
        pages = PostponementPage.objects.live()                          \
                                .filter(date__range=(date_from, date_to))
        for page in pages:
            day_num = page.date.toordinal() - ord_from
            events[day_num].days_events.append(ThisEvent(page.postponement_title,
                                                         page))
        return events

    @property
    def status(self):
        now = dt.datetime.now()
        if datetimeTo(self.date, self.time_to) < now:
            return "finished"
        if self.time_from is not None:
            if dt.datetime.combine(self.date, self.time_from) < now:
                return "started"
        return None

    @property
    def when(self):
        return "{} {}".format(dateFormat(self.date),
                              timeFormat(self.time_from, self.time_to, "at "))

    @property
    def at(self):
        return timeFormat(self.time_from)

    def _getFromDt(self):
        return getDatetime(self.date, self.time_from, dt.time.max)

# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------