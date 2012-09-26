import random
import datetime

from django.db import models

from django.contrib.auth.models import User, Group
from django.utils.encoding import iri_to_uri
from django.utils.translation import ugettext_lazy as _
from django.utils.translation import ugettext_noop

class GroupInfo(Group):
    u"""
    Class that stores additional data on a :class:`Group`.
    """
    
    class Meta:
        app_label = "plmapp"

    description = models.TextField(blank=True)
    creator = models.ForeignKey(User, related_name="%(class)s_creator")
    
    owner = models.ForeignKey(User, verbose_name=_("owner"), 
                              related_name="%(class)s_owner")
    ctime = models.DateTimeField(_("date of creation"), default=datetime.datetime.today,
                                 auto_now_add=False)
    mtime = models.DateTimeField(_("date of last modification"), auto_now=True)

    def __init__(self, *args, **kwargs):
        if "__fake__" not in kwargs:
            super(GroupInfo, self).__init__(*args, **kwargs)

    @property
    def plmobject_url(self):
        return iri_to_uri("/group/%s/" % self.name)

    @property
    def attributes(self):
        u"Attributes to display in `Attributes view`"
        return ["name", "description", "creator", "owner",
                "ctime", "mtime"]

    @property
    def menu_items(self):
        "menu items to choose a view"
        return ["attributes", "history", "users", "objects"]

    @classmethod
    def excluded_creation_fields(cls):
        "Returns fields which should not be available in a creation form"
        return ["owner", "creator", "ctime", "mtime"]

    @classmethod
    def get_creation_fields(cls):
        """
        Returns fields which should be displayed in a creation form.

        By default, it returns :attr:`attributes` less attributes returned by
        :meth:`excluded_creation_fields`
        """
        fields = []
        for field in cls(__fake__=True).attributes:
            if field not in cls.excluded_creation_fields():
                fields.append(field)
        return fields

    @classmethod
    def excluded_modification_fields(cls):
        """
        Returns fields which should not be available in a modification form
        """
        return [ugettext_noop("name"), ugettext_noop("creator"),
                ugettext_noop("owner"), ugettext_noop("ctime"),
                ugettext_noop("mtime")]

    @classmethod
    def get_modification_fields(cls):
        """
        Returns fields which should be displayed in a modification form
              
        By default, it returns :attr:`attributes` less attributes returned by
        :meth:`excluded_modification_fields`
        """
        fields = []
        for field in cls(__fake__=True).attributes:
            if field not in cls.excluded_modification_fields():
                fields.append(field)
        return fields

    @property
    def is_editable(self):
        return True

    def get_attributes_and_values(self):
        return [(attr, getattr(self, attr)) for attr in self.attributes]


class Invitation(models.Model):
    
    class Meta:
        app_label = "plmapp"

    PENDING = "p"
    ACCEPTED = "a"
    REFUSED = "r"
    STATES = ((PENDING, "Pending"),
              (ACCEPTED, "Accepted"),
              (REFUSED, "Refused"))
    group = models.ForeignKey(GroupInfo)
    owner = models.ForeignKey(User, related_name="%(class)s_inv_owner")
    guest = models.ForeignKey(User, related_name="%(class)s_inv_guest")
    state = models.CharField(max_length=1, choices=STATES, default=PENDING)
    ctime = models.DateTimeField(_("date of creation"), default=datetime.datetime.today,
                                 auto_now_add=False)
    validation_time = models.DateTimeField(_("date of validation"), null=True)
    guest_asked = models.BooleanField(_("True if guest created the invitation"))
    token = models.CharField(max_length=155, primary_key=True,
            default=lambda:str(random.getrandbits(512)))