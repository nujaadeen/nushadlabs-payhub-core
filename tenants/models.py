import uuid

from django.db import models


class Tenant(models.Model):
    """
    A Tenant is one customer of *our* platform (e.g. a company using PayHub to
    accept payments). Every other model in this project points back to a
    Tenant, either directly or indirectly, so that one tenant's data is never
    mixed up with another's.

    Django magic: by default, every model gets an implicit `id` field that is
    an auto-incrementing IntegerField (1, 2, 3, ...). We override that here by
    declaring our own `id` field as the primary key.

    Why UUID instead of the default auto-incrementing integer?
    - Multi-tenant safety: integer ids are sequential and guessable/enumerable
      (e.g. an API caller could try /tenants/1/, /tenants/2/, ... and probe
      for data that belongs to other tenants). A UUID is effectively
      impossible to guess.
    - Because we have no auth in this project yet (see settings.py), this is
      one of the few defenses we have against one tenant poking at another
      tenant's records by guessing ids, so it matters more here than usual.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    # auto_now_add=True: Django sets this field to "now" automatically the
    # ONE TIME the row is first created, and then never touches it again.
    # (Compare to auto_now=True, which updates on every save() - we don't
    # want that here since this is a creation timestamp.)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Without this, Django would name the table
        # "tenants_tenant" (<app_label>_<lowercased model name>, no
        # separators between words). We override it with `db_table` to get
        # a clean snake_case name matching our original schema design (and
        # Odoo's own payment.* table naming convention).
        db_table = "tenant"

    def __str__(self):
        return self.name


class Customer(models.Model):
    """
    A Customer is an end-user *of a tenant* - i.e. the person who is actually
    paying. Each tenant manages its own set of customers.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="customers")
    # This is the tenant's OWN identifier for this customer (e.g. the primary
    # key of a "User" row in the tenant's own system). It is deliberately NOT
    # the same as our `id` UUID above - `id` identifies the row in *our*
    # database, `reference` identifies the same person in *their* database.
    reference = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A given tenant cannot have two Customer rows with the same
        # `reference`, but two different tenants can each have a customer
        # with reference "42" without colliding - the uniqueness is scoped
        # per-tenant, not global.
        unique_together = ("tenant", "reference")
        # Without this, Django would name the table "tenants_customer"
        # (<app_label>_<lowercased model name>). We override it with
        # `db_table` to get a clean snake_case name matching our original
        # schema design (and Odoo's own payment.* table naming convention).
        db_table = "customer"

    def __str__(self):
        return f"{self.reference} ({self.tenant_id})"
