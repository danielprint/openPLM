from openPLM.plmapp.base_views import handle_errors, secure_required, get_generic_data
from openPLM.document3D.forms import *
from openPLM.document3D.models import *
from openPLM.document3D.arborescense import *
from openPLM.document3D.classes import *
from openPLM.plmapp.forms import *
from openPLM.plmapp.models import get_all_plmobjects
from django.db import transaction
from django.forms.formsets import formset_factory
from django.http import HttpResponseRedirect, HttpResponseForbidden
from openPLM.plmapp.tasks import update_indexes
from openPLM.plmapp.exceptions import LockError
from openPLM.plmapp.controllers import get_controller
from openPLM.plmapp.decomposers.base import Decomposer, DecomposersManager
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from openPLM.plmapp.views.main import r2r
import tempfile

@handle_errors
def display_3d(request, obj_ref, obj_revi):
    """

    Manage html page which displays the 3d view of the :class:`.DocumentFile` STEP attached to a :class:`.Document3D`.

    For the correct visualization there is necessary to extract all the geometries contained in the :class:`~django.core.files.File` **.geo** present in the :class:`.GeometryFile` relative to :class:`.DocumentFile` that we want to show and also the **.geo** contained in to the :class:`.DocumentFile` in which the DocumentFile to show has been decomposed.
    
    We need to generate also the code javascript to manage these geometries.
    To generate this code we need the information about the arborescense of the :class:`.DocumentFile` , this arborescense is obtained of the :class:`~django.core.files.File` **.arb** corresponding to the :class:`.ArbreFile` connected to the :class:`.DocumentFile`.The information the file **.arb** turns into a :class:`.Product` that we use to call  the function :meth:`.generate_javascript_for_3D` that return the code required

    """

    obj_type = "Document3D"
    obj, ctx = get_generic_data(request, obj_type, obj_ref, obj_revi)
    ctx['current_page'] = '3D'

    try:
        doc_file = obj.files.filter(is_stp)[0]
    except IndexError:
        doc_file = None

    if doc_file is None:
        GeometryFiles=[]
        javascript_arborescense=False
    else:

        product=ArbreFile_to_Product(doc_file,recursif=True)
        GeometryFiles=list(GeometryFile.objects.filter(stp=doc_file))
        if product:  
            add_child_GeometryFiles(product,GeometryFiles)

        
        javascript_arborescense=generate_javascript_for_3D(product)

    ctx.update({
        'GeometryFiles' : GeometryFiles ,
        'javascript_arborescense' : javascript_arborescense , })

    return r2r('Display3D.htm', ctx, request)


class StepDecomposer(Decomposer):

    __slots__ = ("part", "decompose_valid")

    def is_decomposable(self, msg=True):
        decompose_valid = []
        if not Document3D.objects.filter(PartDecompose=self.part).exists():
            links = DocumentPartLink.objects.filter(part=self.part,
                    document__type="Document3D",
                    document__document3d__PartDecompose=None).values_list("document", flat=True)
            for doc_id in links:
                try:
                    if msg:
                        doc = Document3D.objects.get(id=doc_id)
                    else:
                        doc = doc_id
                    file_stp = is_decomposable(doc)
                    if file_stp and msg:
                        decompose_valid.append((doc, file_stp))
                    elif file_stp:
                        return True
                except:
                    pass

        else:
            try:

                doc = Document3D.objects.get(PartDecompose=self.part)
                file_stp = is_decomposable(doc)
                if file_stp and msg:
                    decompose_valid.append((doc, file_stp))
                elif file_stp:
                    return True
            except:
                pass 
      
        
        self.decompose_valid = decompose_valid
        return len(decompose_valid) > 0

    def get_decomposable_parts(self, parts):
        decomposable = set()
        # invalid parts are parts already decomposed by a StepDecomposer
        invalid_parts = Document3D.objects.filter(PartDecompose__in=parts)\
                .values_list("PartDecompose", flat=True)
        links = list(DocumentPartLink.objects.filter(part__in=parts,
                document__type="Document3D", # Document3D has no subclasses
                document__document3d__PartDecompose=None). \
                exclude(part__in=invalid_parts).values_list("document", "part"))
        docs = [l[0] for l in links]
        # valid documents are document with a step file that is decomposable
        valid_docs = dict(ArbreFile.objects.filter(stp__document__in=docs,
            stp__deprecated=False, stp__locked=False,
            decomposable=True).values_list("stp__document", "stp"))
        for doc_id, part_id in links:
            if (doc_id not in valid_docs) or (part_id in decomposable):
                continue
            stp = DocumentFile.objects.only("document", "filename").get(id=valid_docs[doc_id])
            if stp.checkout_valid:
                decomposable.add(part_id)
        return decomposable

    def get_message(self):
        if self.decompose_valid:
            return render_to_string("decompose_msg.html", { "part" : self.part,
                "decomposable_docs" : self.decompose_valid })
        return ""

DecomposersManager.register(StepDecomposer)
#posibilidades , el objeto a sido modificado despues de acceder al formulario

Select_Doc_Part_types = formset_factory(Doc_Part_type_Form, extra=0)
Select_Order_Quantity_types = formset_factory(Order_Quantity_Form, extra=0)
#@handle_errors
def display_decompose(request, obj_type, obj_ref, obj_revi, stp_id):


    """
    :param obj_type: Type of the :class:`.Part` from which we want to realize the decomposition
    :param obj_ref: Reference of the :class:`.Part` from which we want to realize the decomposition
    :param obj_revi: Revision of the :class:`.Part` from which we want to realize the decomposition
    :param stp_id: Id that identify the :class:`.DocumentFile` contained in a :class:`.Document3D` attached to the :class:`.Part` (identified by **obj_type**, **obj_ref**, **obj_revi**) that we will decompose 
    
    
    When we demand the decomposition across the web form, the following tasks are realized
    
    -We check that the :class:`.Document3D` that contains the :class:`.DocumentFile` (**stp_id**) that will be decomposed has not been modified since the generation of the form
    
    -We check the validity of the information got in the form
    
    -If exists a :class:`.DocumentFile` native file related to :class:`.DocumentFile` (**stp_id**) that will be decomposed
    
        -then this one was depreciated (afterwards will be promoted)
        
    -The :class:`.DocumentFile` (**stp_id**) was locked (afterwards will be promoted)
    
    
    
    -We call the function :meth:`.generate_part_doc_links_AUX` (with the property transaction.commit_on_success)
           
        -We generate the arborescense (:class:`.product`) of the :class:`.DocumentFile` (**stp_id**))
        
        -The bomb-child of Parts (in relation to the arborescense of the :class:`.DocumentFile` (**stp_id**)) has been generated
        
        -For every :class:`.ParentChildLink` generated in the previous condition  we attach all the :class:`.Location_link` relatives
        
        -To every generated :class:`.Part` a :class:`.Document3D` has been attached and this document as been set like the attribute PartDecompose of the :class:`.Part`
         
        -The attribute doc_id of every node of the arborescense (:class:`.Product`) is now the relative id of :class:`.Document3D` generated in the previous condition
        
        -To every generated :class:`.Document3D` has been added a new empty(locked) :class:`.DocumentFile` STP
        
        -The attribute doc_path of every node of the arborescense(:class:`.Product`) is now the path of :class:`.DocumentFile` STP generated in the previous condition
        
    -We update the indexes for the objects generated
    
    -We call the processus decomposer_all(with celeryd)         
            
  

    """

    obj, ctx = get_generic_data(request, obj_type, obj_ref, obj_revi)
    stp_file=DocumentFile.objects.get(id=stp_id)
    assemblys=[]
    doc_linked_to_part=obj.get_attached_documents().values_list("document_id", flat=True)
    
    if stp_file.locked:
        raise ValueError("Not allowed operation.This DocumentFile is locked")
    if not stp_file.document_id in doc_linked_to_part:
        raise ValueError("Not allowed operation.The Document and the Part are not linked")
    if Document3D.objects.filter(PartDecompose=obj.object).exists() and not Document3D.objects.get(PartDecompose=obj.object).id==stp_file.document.id: #a same document could be re-decomposed for the same part
        
        raise ValueError("Not allowed operation.This Part already forms a part of another decomposition ")
    try:
        doc3D=Document3D.objects.get(id=stp_file.document_id)
    except Document3D.DoesNotExist:
        raise ValueError("Not allowed operation.The document is not a subtype of document3D")

    if doc3D.PartDecompose and not doc3D.PartDecompose.id==obj.object.id:
        raise ValueError("Not allowed operation.This Document already forms a part of another decomposition")
        


    if request.method == 'POST':
    

        extra_errors=""
        product=ArbreFile_to_Product(stp_file,recursif=None)
        last_time_modification=Form_save_time_last_modification(request.POST)
        obj.block_mails()

        if last_time_modification.is_valid() and product:
            old_modification_data_time=last_time_modification.cleaned_data['last_modif_time']
            old_modification_data_microsecond=last_time_modification.cleaned_data['last_modif_microseconds']


            document_controller=DocumentController(stp_file.document,User.objects.get(username=settings.COMPANY))
            index=[1]
            if clear_form(request,assemblys,product,index,obj_type):

                if (same_time(old_modification_data_time, 
                              old_modification_data_microsecond,
                              document_controller.mtime)
                    and stp_file.checkout_valid and not stp_file.locked):
                    


                   
                    stp_file.locked=True
                    stp_file.locker=User.objects.get(username=settings.COMPANY)
                    stp_file.save(False)


                    
                    native_related=stp_file.native_related                       
                    if native_related:
                        native_related.deprecated=True
                        native_related.save(False)
                        native_related_pk=native_related.pk
                    else:
                        native_related_pk=None


                    try:
                        instances=[]
                        generate_part_doc_links_AUX(request,product, obj,instances)
                        update_indexes.delay(instances) 
                    except Exception as excep:
                        if type(excep) == Document_Generate_Bom_Error:
                            delete_files(excep.to_delete)

                        

                        extra_errors = unicode(excep)
                        stp_file.locked = False
                        stp_file.locker = None
                        stp_file.save(False)
                        if native_related:
                            native_related.deprecated=False
                            native_related.save(False)
                    else:
            
                        decomposer_all.delay(stp_file.pk,json.dumps(data_for_product(product)),obj.object.pk,native_related_pk,obj._user.pk)
                        
                        return HttpResponseRedirect(obj.plmobject_url+"BOM-child/")
  




                else:

                    extra_errors="The Document3D associated with the file STEP to decompose has been modified by another user while the forms were refilled:Please restart the process"
                
            else:

                extra_errors="Mistake refilling the form, please check it"

        else:

            extra_errors="Mistake reading of the last modification of the document, please restart the task"

    else:

        document_controller=DocumentController(stp_file.document,request.user)
        last_time_modification=Form_save_time_last_modification()
        last_time_modification.fields["last_modif_time"].initial=document_controller.mtime

        last_time_modification.fields["last_modif_microseconds"].initial=document_controller.mtime.microsecond
        product=ArbreFile_to_Product(stp_file,recursif=None)
        if not product or not product.links:
            return HttpResponseRedirect(obj.plmobject_url+"BOM-child/")
        
        group = obj.group
        index=[1,0] # index[1] to evade generate holes in part_revision_default generation
        initialiser_assemblys(assemblys,product,group,request.user,index,obj_type)
        

        extra_errors = ""
        

    deep_assemblys=sort_assemblys_by_depth(assemblys)
    ctx.update({'current_page':'decomposer',  # aqui cambiar
                'deep_assemblys' : deep_assemblys,
                'extra_errors' :  extra_errors ,
                'last_time_modification' : last_time_modification

                })

    return r2r('DisplayDecompose.htm', ctx, request)
    
    
def sort_assemblys_by_depth(assemblys):

    new_assembly=[]
    for elem in assemblys:
        for i in range(elem[3]+1-len(new_assembly)):
            new_assembly.append([])        
        new_assembly[elem[3]].append(elem)

    return new_assembly             

    

def clear_form(request,assemblys, product,index,obj_type):

    """
    
    :param assemblys: will be refill whit the information necessary the generate the forms
    :param product: :class:`.Product` that represents the arborescense of the :class:`~django.core.files.File` .stp contained in a :class:`.DocumentFile`
    :param index: Use  to mark and to identify the **product** s that already have been visited
    :param obj_type: Type of the :class:`.Part` from which we want to realize the decomposition 
    
    It checks the validity of the forms contained in **request**
    
    
    
    If the forms are not valide, he returns the information to refill the new forms contained in **assemblys**.
     
    Refill **assemblys** with the different assemblys of the file step , we use **index** to mark and to identify the **product** s that already have been visited
        
    For every Assembly we have the next information:
    
        -Name of assembly
         
        -Visited , If assembly is sub-assembly of more than an assembly, this attribute will be **False** for all less one of the occurrences
        
            If visited is **False**, we will be able to modify only the attributes **Order** , **Quantity** and **Unit** refered to the :class:`.ParentChildLink` in the form
            
            If visited is not **False** , it will be a new id acording to **index** (>=1) generated to identify the assembly
            
        -Depth  of assembly
         
        -**obj_type** , type of :class:`.Part` of Assembly
        
        -A list with the products that compose the assembly
        
            for each element in the list:
            
                -part_type contains the form to select the type of :class:`.Part`  
                
                -ord_quantity contains the forms to select Order , Quantity and Unit refered to the :class:`.ParentChildLink`
                
                -creation_formset contains the form for the creation of the part selected in part_type and of one :class:`.Document3D`
                
                -name_child_assemblys contains the name of the element
                
                -is_assembly determine if the element is a single product or another assembly 
                
                -prefix contains the **index** of the assembly  if he is visited for first time , else is False
                
                -ref contains the **index** of the assembly if he was visited previously, else False 
                   
    """

    creation_formset=[]
    initial_bom_values=[]
    initial_deep_values=[]
    name_child_assemblys=[]
    is_assembly=[]
    part_type=[]
    ord_quantity=[]
    prefix=[]
    ref=[]    
    valid=True
    if product.links:
        for link in product.links:
        
            
            ord_qty=Order_Quantity_Form(request.POST,prefix=index[0])
            link.visited=index[0]


            if not ord_qty.is_valid():
                valid=False
     
            ord_quantity.append(ord_qty)
            is_assembly.append(link.product.is_assembly)
            name_child_assemblys.append(link.product.name)
                        
            if not link.product.visited:
                link.product.visited=index[0]
                  
                part_ctype=Doc_Part_type_Form(request.POST,prefix=index[0])
                if not part_ctype.is_valid():
                    valid=False        
                options=part_ctype.cleaned_data
                part = options["type_part"]
                cls = get_all_plmobjects()[part]
                part_form = get_creation_form(request.user, cls, request.POST,
                        prefix=str(index[0])+"-part")            
                doc_form = get_creation_form(request.user, Document3D,
                        request.POST, prefix=str(index[0])+"-document")                    
                if not part_form.is_valid():
                    valid=False
                if not doc_form.is_valid():
                    print "4"
                    valid=False               

                prefix.append(index[0])
                creation_formset.append([part_form, doc_form])                                
                ref.append(None)          
                part_type.append(part_ctype) 
                               
                index[0]+=1            

                                              
                if not clear_form(request, assemblys, link.product,index , part):
                    valid=False
            else:
                index[0]+=1 
                part_type.append(False);creation_formset.append(False);prefix.append(False);ref.append(link.product.visited)
        
        

        
        
                                
        assemblys.append((zip(part_type ,ord_quantity,  creation_formset,  name_child_assemblys , is_assembly , prefix , ref )  , product.name , product.visited , product.deep, obj_type))            
    return valid                                    
        
        
   
def initialiser_assemblys(assemblys,product,group,user,index, obj_type):
    """
    
    :param assemblys: will be refill whit the information necessary the generate the forms
    :param product: :class:`.Product` that represents the arborescense of the :class:`~django.core.files.File` .stp contained in a :class:`.DocumentFile`
    :param index: Use  to mark and to identify the **product** s that already have been visited
    :param obj_type: Type of the :class:`.Part` from which we want to realize the decomposition 
    :param group: group by default from which we want to realize the decomposition 
    
            
    Returns in assemblys a list initialized with the different assemblies of the file step
        
        
        
    For every Assembly we have the next information:
    
        -Name of assembly
         
        -Visited , If assembly is sub-assembly of more than an assembly, this attribute will be **False** for all less one of the occurrences
        
            If visited is **False**, we will be able to modify only the attributes **Order** , **Quantity** and **Unit** refered to the :class:`.ParentChildLinkin` in the form
            
            If visited is not **False** , it will be a new id acording to **index** (>=1) generated to identify the assembly
            
        -Depth  of assembly
         
        -**obj_type** , type of :class:`.Part` of Assembly
        
        -A list with the products that compose the assembly
        
            for each element in the list:
            
                -part_type contains the form to select the type of :class:`.Part`  
                
                -ord_quantity contains the forms to select Order , Quantity and Unit refered to the :class:`.ParentChildLink`
                
                -creation_formset contains the form for the creation of the part selected in part_type and of one :class:`.Document3D`
                
                -name_child_assemblys contains the name of the element
                
                -is_assembly determine if the element is a single product or another assembly 
                
                -prefix contains the **index** of the assembly if he is visited for first time , else is False
                
                -ref contains the **index** of the assembly if he was visited previously, else False 

                     
    """ 
    creation_formset=[]
    initial_bom_values=[]
    initial_deep_values=[]
    name_child_assemblys=[]
    is_assembly=[]
    part_type=[]
    ord_quantity=[]
    prefix=[]
    ref=[]
    if product.links:
        for order , link in enumerate(product.links):
            
            oq=Order_Quantity_Form(prefix=index[0])

            oq.fields["order"].initial=(order+1)*10
            oq.fields["quantity"].initial=link.quantity
            ord_quantity.append(oq)
            is_assembly.append(link.product.is_assembly)
            name_child_assemblys.append(link.product.name)
            if not link.product.visited: 
                link.product.visited=index[0]           
                part_type.append(Doc_Part_type_Form(prefix=index[0])) 
                part_cform = get_creation_form(user, Part, None, (index[1])) # index[0].initial=1 -> -1
                part_cform.prefix = str(index[0])+"-part"
                part_cform.fields["group"].initial = group
                part_cform.fields["name"].initial = link.product.name
                doc_cforms = get_creation_form(user, Document3D, None, (index[1]))
                doc_cforms.prefix = str(index[0])+"-document"
                doc_cforms.fields["name"].initial = link.product.name 
                doc_cforms.fields["group"].initial = group
                prefix.append(index[0])
                creation_formset.append([part_cform, doc_cforms])                                

                ref.append(None)
                index[0]+=1
                index[1]+=1                   
                initialiser_assemblys(assemblys,link.product,group,user,index, "Part")                 
            else:
                index[0]+=1 
                part_type.append(False);creation_formset.append(False);prefix.append(False);ref.append(link.product.visited)
                   


        assemblys.append((zip(part_type ,ord_quantity,  creation_formset,  name_child_assemblys , is_assembly , prefix , ref )  , product.name , product.visited ,product.deep, obj_type))

@transaction.commit_on_success
def generate_part_doc_links_AUX(request,product, parent_ctrl,instances):  # para generar bien el commit on succes

    generate_part_doc_links(request,product, parent_ctrl,instances)
         
def generate_part_doc_links(request,product, parent_ctrl,instances):

    """
    

    :param product: :class:`.Product` that represents the arborescense
    :param parent_ctrl: :class:`.Part` from which we want to realize the decomposition
    :param instances: Use to trace the items to update 

        
    He reads the forms and generates:
    
    
    -The bomb-child of Parts (in relation to the **product**) 
    
    -For every :class:`.ParentChildLink` generated in the condition previous we attach all the :class:`.Location_link` relatives
    
    -To every generated :class:`.Part` a :class:`.Document3D` has been attached and Document3D as been set like the attribute PartDecompose of the Part
     
    -The attribute doc_id of every node of the arborescense(**product**) is now the relative id of :class:`.Document3D` generated in the previous condition
    
    -To every generated :class:`.Document3D` has been added a new empty(locked) :class:`.DocumentFile` STP ( :meth:`.generateGhostDocumentFile` )
    
    -The attribute doc_path of every node of the arborescense(**product**) is now the path of :class:`.DocumentFile` STP generated in the previous condition
    """
    
    to_delete=[]
    user = parent_ctrl._user
     

    for link in product.links: 
        try:   

            oq=Order_Quantity_Form(request.POST,prefix=link.visited)
            oq.is_valid();options=oq.cleaned_data          
            order=options["order"];quantity=options["quantity"];unit=options["unit"]
            
            if not link.product.part_to_decompose: 
            

                part_ctype=Doc_Part_type_Form(request.POST,prefix=link.product.visited)
                part_ctype.is_valid();options=part_ctype.cleaned_data
                cls = get_all_plmobjects()[options["type_part"]]
                part_form = get_creation_form(user, cls, request.POST,
                            prefix=str(link.product.visited)+"-part") 
                         
                part_ctrl = parent_ctrl.create_from_form(part_form, user, True, True)

                instances.append((part_ctrl.object._meta.app_label,
                    part_ctrl.object._meta.module_name, part_ctrl.object._get_pk_val()))

                c_link = parent_ctrl.add_child(part_ctrl.object,quantity,order,unit)

                generate_extra_location_links(link, c_link)

                doc_form = get_creation_form(user, Document3D,
                        request.POST, prefix=str(link.product.visited)+"-document")              
                doc_ctrl = Document3DController.create_from_form(doc_form,
                        user, True, True)
                        
                link.product.part_to_decompose=part_ctrl.object
                to_delete.append(generateGhostDocumentFile(link.product,doc_ctrl))

 
                instances.append((doc_ctrl.object._meta.app_label,
                    doc_ctrl.object._meta.module_name, doc_ctrl.object._get_pk_val()))
                part_ctrl.attach_to_document(doc_ctrl.object)
                
                                    
                Doc3D=Document3D.objects.get(id=doc_ctrl.object.id)
                Doc3D.PartDecompose=part_ctrl.object
                Doc3D.no_index=True 
                Doc3D.save()

                generate_part_doc_links(request,link.product, part_ctrl,instances)

                    
            else:
            
                c_link = parent_ctrl.add_child(link.product.part_to_decompose,quantity,order,unit)
                generate_extra_location_links(link, c_link)
                
            

        except Exception as excep:
            #raise excep
            raise Document_Generate_Bom_Error(to_delete,link.product.name)    
    


            
 


    
def generateGhostDocumentFile(product,Doc_controller):
    """
    :param product: :class:`.Product` that represents the arborescense
    :param Doc_controller: :class:`.Document3DController` from which we want to generate the :class:`.DocumentFile` 

    
    For one :class:`.Product` (**product**) and one :class:`.Document3DController` (**Doc_controller**)generates a :class:`.DocumentFile` with a file .stp emptily without indexation
    
    It updates the attributes **doc_id** and **doc_path** of the :class:`.Product` (**product**) in relation of the generated :class:`.DocumentFile`
    
    """
    doc_file=DocumentFile()
    name = doc_file.file.storage.get_available_name(product.name+".stp")
    path = os.path.join(doc_file.file.storage.location, name)
    f = File(open(path.encode(), 'w'))
    f.close()   

    Doc_controller.check_permission("owner")
    Doc_controller.check_editable()
    
    if settings.MAX_FILE_SIZE != -1 and f.size > settings.MAX_FILE_SIZE:
        raise ValueError("File too big, max size : %d bytes" % settings.MAX_FILE_SIZE)
            
    if Doc_controller.has_standard_related_locked(f.name):
        raise ValueError("Native file has a standard related locked file.") 
           
    doc_file.no_index=True        
    doc_file.filename="Ghost.stp"
    doc_file.size=f.size
    doc_file.file=name
    doc_file.document=Doc_controller.object
    doc_file.locked = True
    doc_file.locker = User.objects.get(username=settings.COMPANY)
    doc_file.save()  
    
    

    product.doc_id=doc_file.id

    product.doc_path=doc_file.file.path 


    return doc_file.file.path

    


@secure_required
@login_required
def ajax_part_creation_form(request, prefix):
    """
    It updates the form of an assembly determined by **prefix** without recharging the whole page and respecting the information introduced up to the moment
    
    The attributes can change depending on the type of part selected

    """
    tf = Doc_Part_type_Form(request.GET, prefix=prefix)

    if tf.is_valid():

        cls = get_all_parts()[tf.cleaned_data["type_part"]]

        cf = get_creation_form(request.user, cls, prefix=prefix+"-part",
                data=dict(request.GET.iteritems()))

        return r2r("extra_attributes.html", {"creation_form" : cf}, request)

    return HttpResponseForbidden()
    
   

def same_time(old_modification_data,old_modification_data_microsecond,mtime):

    return (old_modification_data_microsecond == mtime.microsecond
            and old_modification_data.second == mtime.second
            and old_modification_data.minute == mtime.minute
            and old_modification_data.hour == mtime.hour
            and old_modification_data.date()==mtime.date())

########################################################################



