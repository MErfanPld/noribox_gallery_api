from django.db import migrations


class Migration(migrations.Migration):
    """Rename the prototype Product model to SourceProduct (data preserved)."""

    dependencies = [
        ("categories", "0001_initial"),
        ("products", "0002_product_categories"),
    ]

    operations = [
        migrations.RenameModel("Product", "SourceProduct"),
    ]
