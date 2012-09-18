import kjbuckets


from django.db import models
from django.db.models.query import QuerySet
from django.contrib.auth.models import User

from openPLM.plmapp.units import UNITS, DEFAULT_UNIT
from openPLM.plmapp.utils import level_to_sign_str

from .plmobject import PLMObject
from .part import Part
from .document import Document

class Link(models.Model):
    u"""
    Abstract link base class.

    This class represents a link between two :class:`.PLMObject`
    
    :model attributes:
        .. attribute:: ctime

            date of creation of the link (automatically set)

    :class attributes:
        .. attribute:: ACTION_NAME

            an identifier used to set :attr:`.History.action` field
    """

    ctime = models.DateTimeField(auto_now_add=True)

    ACTION_NAME = "Link"
    class Meta:
        abstract = True  

class ParentChildLink(Link):
    """
    Link between two :class:`.Part`: a parent and a child
    
    :model attributes:
        .. attribute:: parent

            a :class:`.Part`
        .. attribute:: child

            a :class:`.Part`
        .. attribute:: quantity
            
            amount of child (a positive float)
        .. attribute:: unit
            
            unit of the quantity
        .. attribute:: order
            
            positive integer
        .. attribute:: end_time
            
            date of end of the link, None if the link is still alive

    """

    ACTION_NAME = "Link : parent-child"

    parent = models.ForeignKey(Part, related_name="%(class)s_parent")    
    child = models.ForeignKey(Part, related_name="%(class)s_child")    
    quantity = models.FloatField(default=lambda: 1)
    unit = models.CharField(max_length=4, choices=UNITS,
            default=lambda: DEFAULT_UNIT)
    order = models.PositiveSmallIntegerField(default=lambda: 1)
    end_time = models.DateTimeField(blank=True, null=True, default=lambda: None)
    
    class Meta:
        app_label = "plmapp"
        unique_together = ("parent", "child", "end_time")

    def __unicode__(self):
        return u"ParentChildLink<%s, %s, %f, %s, %d>" % (self.parent, self.child,
                                 self.quantity, self.unit, self.order)

    def get_shortened_unit(self):
        """ Returns unit as a human readable string.
        If :attr:`.unit` equals to "-", returns an empty string.
        """
        if self.unit == "-":
            return u""
        return self.get_unit_display()

    @property
    def extensions(self):
        """ Returns a queryset of bound :class:`.ParentChildLinkExtension`. """
        return ParentChildLinkExtension.children.filter(link=self)

    def get_extension_data(self):
        """
        Returns a dictionary of extension data. The returned value can be passed
        as a valid arguement to :meth:`.clone`.
        """

        extension_data = {}
        for ext in self.extensions:
            if ext.one_per_link():
                extension_data[ext._meta.module_name] = ext.to_dict()
        return extension_data

    def clone(self, save=False, extension_data=None, **kwargs):
        u"""
        Clone this link.

        It is possible to pass additional arguments to override some original
        values.

        :param save: If True, the cloned link and its extensions are saved
        :param extension_data: dictionary PCLE module name -> data of data
            that are given to :meth:`.ParentChildLinkExtension.clone`.
        
        :return: a tuple (cloned link, list of cloned extensions)

        Example::

            >>> print link
            ParentChildLink<Part<PART_2/MotherBoard/a>, Part<ttd/RAM/a>, 4.000000, -, 10>
            >>> link.extensions
            [<ReferenceDesignator: ReferenceDesignator<m1,m2,>>]
            >>> clone, ext = link.clone(False,
            ...    {"referencedesignator" : { "reference_designator" : "new_value"}},
            ...    quantity=51)
            >>> print clone
            ParentChildLink<Part<PART_2/MotherBoard/a>, Part<ttd/RAM/a>, 51.000000, -, 10>
            >>> print ext
            [<ReferenceDesignator: ReferenceDesignator<new_value>>]
            
        """
        # original data
        data = dict(parent=self.parent, child=self.child,
                quantity=self.quantity, order=self.order, unit=self.unit,
                end_time=self.end_time)
        # update data from kwargs
        for key, value in kwargs.iteritems():
            if key in data:
                data[key] = value
        link = ParentChildLink(**data)
        if save:
            link.save()
        # clone the extensions
        extensions = []
        extension_data = extension_data or {}
        for ext in self.extensions:
            extensions.append(ext.clone(link, save, 
                **extension_data.get(ext._meta.module_name, {})))
        return link, extensions


class ChildQuerySet(QuerySet):
    def iterator(self):
        for obj in super(ChildQuerySet, self).iterator():
            yield obj.get_child_object()


class ChildManager(models.Manager):
    def get_query_set(self):
        return ChildQuerySet(self.model)


class ParentModel(models.Model):
    _child_name = models.CharField(max_length=100, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self._child_name = self.get_child_name()
        super(ParentModel, self).save(*args, **kwargs)

    def get_child_name(self):
        if type(self) is self.get_parent_model():
            return self._child_name
        return self.get_parent_link().related_query_name()

    def get_child_object(self):
        return getattr(self, self.get_child_name())

    def get_parent_link(self):
        return self._meta.parents[self.get_parent_model()]

    def get_parent_model(self):
        raise NotImplementedError

    def get_parent_object(self):
        return getattr(self, self.get_parent_link().name)

registered_PCLEs = []
class ParentChildLinkExtension(ParentModel):
    """
    Extension of a :class:`.ParentChildLink` used to store additional data.

    This class is abstract, subclass must define the :meth:`.clone` method,
    add at least one field (or it would be useless) and may override
    :meth:`.get_visible_fields` or :meth:`.get_editable_fields`.

    .. seealso::
    
        :ref:`bom_extensions` explains how to subclass this class.
    """

    class Meta:
        app_label = "plmapp"

    #! link bound to the PCLE
    link = models.ForeignKey(ParentChildLink, related_name="%(class)s_link")

    objects = models.Manager()
    children = ChildManager()

    @classmethod
    def get_visible_fields(cls):
        """
        Returns the list of visible fieldnames.
        
        By default, returns an empty list.
        """
        return []

    @classmethod
    def get_editable_fields(cls):
        """
        Returns the list of editable fields.

        By default, returns :meth:`.get_visible_fields`.
        """
        return list(cls.get_visible_fields())

    @classmethod
    def one_per_link(cls):
        """ Returns True if only one extension should be created per link.

        By default return True if :meth:`.get_visible_fields` returns a
        non empty list."""
        return bool(cls.get_visible_fields())
    
    @classmethod
    def apply_to(cls, parent):
        """
        Returns True if this extension applies to *parent*.

        :param parent: part which will have a new child
        :type parent: :class:`.Part` (its most specific subclass).
        
        Returns True by default.
        """
        return True

    def clone(self, link, save=False, **data):
        """
        Clone this extension.
        
        **Subclass must define its implementation.** and respect the
        following specification:

        :param link: the new cloned link, the cloned extension must be
                     bound to it
        :type link: :class:`.ParentChildLink`
        :param save: True if the cloned extension must be saved, False
                     (the default) if it must not be saved.
        :type save: boolean
        :param data: additional data that override the original values
        
        :return: the cloned extension
        """
        raise NotImplementedError

    def get_parent_model(self):
        return ParentChildLinkExtension

    def to_dict(self):
        """
        Returns a dictionary fieldnames -> value that can be safely passed as
        a kwargument to :meth:`.clone` and that is used to compare two
        extensions. 
        """
        d = {}
        for field in self._meta.get_all_field_names():
            if field not in ("id", "link", "_child_name",
                    'parentchildlinkextension_ptr'):
                d[field] = getattr(self, field)
        return d
    
def register_PCLE(PCLE):
    """
    Register *PCLE* so that openPLM can show its visible fields.

    :param PCLE: the registered PCLE
    :type PCLE: a subclass of :class:`.ParentChildLinkExtension`.
    """
    registered_PCLEs.append(PCLE)

def get_PCLEs(parent):
    """
    Returns the list of registered :class:`.ParentChildLinkExtension` that
    applied to *parent*.
    """
    return [PCLE for PCLE in registered_PCLEs if PCLE.apply_to(parent)]


class RevisionLink(Link):
    """
    Link between two revisions of a :class:`.PLMObject`
    
    :model attributes:
        .. attribute:: old

            old revision (a :class:`.PLMObject`)
        .. attribute:: new

            new revision (a :class:`.PLMObject`)
    """
    
    class Meta:
        app_label = "plmapp"
        unique_together = ("old", "new")
    
    ACTION_NAME = "Link : revision"
    old = models.ForeignKey(PLMObject, related_name="%(class)s_old")    
    new = models.ForeignKey(PLMObject, related_name="%(class)s_new")
    
    def __unicode__(self):
        return u"RevisionLink<%s, %s>" % (self.old, self.new)
 
class DocumentPartLink(Link):
    """
    Link between a :class:`.Part` and a :class:`.Document`
    
    :model attributes:
        .. attribute:: part

            a :class:`.Part`
        .. attribute:: document

            a :class:`.Document`
    """

    ACTION_NAME = "Link : document-part"

    document = models.ForeignKey(Document, related_name="%(class)s_document")    
    part = models.ForeignKey(Part, related_name="%(class)s_part")    

    class Meta:
        app_label = "plmapp"
        unique_together = ("document", "part")

    def __unicode__(self):
        return u"DocumentPartLink<%s, %s>" % (self.document, self.part)

# abstraction stuff
ROLE_NOTIFIED = "notified"
ROLE_SIGN = "sign_"
ROLE_OWNER = "owner"
ROLE_SPONSOR = "sponsor"

ROLES = [ROLE_OWNER, ROLE_NOTIFIED, ROLE_SPONSOR]
for i in range(10):
    level = level_to_sign_str(i)
    ROLES.append(level)
ROLE_READER = "reader"
ROLES.append(ROLE_READER)

class DelegationLink(Link):
    """
    Link between two :class:`~.django.contrib.auth.models.User` to delegate
    his rights (abstract class)
    
    :model attributes:
        .. attribute:: delegator

            :class:`~django.contrib.auth.models.User` who gives his role
        .. attribute:: delegatee

            :class:`~django.contrib.auth.models.User` who receives the role
        .. attribute:: role
            
            right that is delegated
    """

    ACTION_NAME = "Link : delegation"
    
    delegator = models.ForeignKey(User, related_name="%(class)s_delegator")    
    delegatee = models.ForeignKey(User, related_name="%(class)s_delegatee")    
    role = models.CharField(max_length=30, choices=zip(ROLES, ROLES),
            db_index=True)

    class Meta:
        app_label = "plmapp"
        unique_together = ("delegator", "delegatee", "role")

    def __unicode__(self):
        return u"DelegationLink<%s, %s, %s>" % (self.delegator, self.delegatee,
                                                self.role)
    
    @classmethod
    def get_delegators(cls, user, role):
        """
        Returns the list of user's id of the delegators of *user* for the role
        *role*.
        """
        links = cls.objects.filter(role=role).values_list("delegatee", "delegator")
        gr = kjbuckets.kjGraph(tuple(links))
        return gr.reachable(user.id).items()


class PLMObjectUserLink(Link):
    """
    Link between a :class:`~.django.contrib.auth.models.User` and a
    :class:`.PLMObject`
    
    :model attributes:
        .. attribute:: plmobject

            a :class:`.PLMObject`
        .. attribute:: user

            a :class:`.User`
        .. attribute:: role
            
            role of *user* for *plmobject* (like `owner` or `notified`)
    """

    ACTION_NAME = "Link : PLMObject-user"

    plmobject = models.ForeignKey(PLMObject, related_name="%(class)s_plmobject")    
    user = models.ForeignKey(User, related_name="%(class)s_user")    
    role = models.CharField(max_length=30, choices=zip(ROLES, ROLES),
            db_index=True)

    class Meta:
        app_label = "plmapp"
        unique_together = ("plmobject", "user", "role")
        ordering = ["user", "role", "plmobject__type", "plmobject__reference",
                "plmobject__revision"]

    def __unicode__(self):
        return u"PLMObjectUserLink<%s, %s, %s>" % (self.plmobject, self.user, self.role)


