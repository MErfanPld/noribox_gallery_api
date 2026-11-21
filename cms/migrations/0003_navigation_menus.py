from django.db import migrations


def create_menus(apps, schema_editor):
    Menu = apps.get_model("cms", "NavigationMenu")
    Menu.objects.get_or_create(location="header", defaults={"name": "منوی اصلی (هدر)"})
    Menu.objects.get_or_create(location="footer", defaults={"name": "منوی فوتر"})


class Migration(migrations.Migration):
    dependencies = [("cms", "0002_admin_crm")]
    operations = [migrations.RunPython(create_menus, migrations.RunPython.noop)]
