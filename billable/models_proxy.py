from django.contrib.auth import get_user_model
from .models import QuotaBatch, Order, Transaction

User = get_user_model()

class Customer(User):
    """
    Proxy model for User to provide a dedicated 'Customer' interface in Admin.
    Does not create a new table.
    """
    class Meta:
        proxy = True
        verbose_name = "Customer"
        verbose_name_plural = "Customers"
