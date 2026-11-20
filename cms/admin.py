from django.contrib import admin

from cms.models import FAQ, Banner, BlogCategory, BlogPost, Page, SEOMetadata, Slide, Slider, StoreSettings


class SlideInline(admin.TabularInline):
    model = Slide
    extra = 0


@admin.register(Slider)
class SliderAdmin(admin.ModelAdmin):
    inlines = [SlideInline]


for model in (Page, BlogCategory, BlogPost, FAQ, Banner, SEOMetadata, StoreSettings):
    admin.site.register(model)
