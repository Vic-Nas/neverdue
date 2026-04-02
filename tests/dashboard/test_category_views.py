import pytest
import json
from django.urls import reverse
from dashboard.models import Category


@pytest.fixture
def categories(user):
    return [
        Category.objects.create(user=user, name='School'),
        Category.objects.create(user=user, name='Work'),
    ]


@pytest.mark.django_db
class TestCategoriesList:
    def test_renders(self, auth_client, categories):
        resp = auth_client.get(reverse('dashboard:categories'))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestCategoryEdit:
    def test_create(self, auth_client):
        resp = auth_client.post(reverse('dashboard:category_create'), {
            'name': 'Gym', 'priority': '2',
        })
        assert resp.status_code == 302
        assert Category.objects.filter(name='Gym').exists()

    def test_empty_name_rejected(self, auth_client):
        resp = auth_client.post(reverse('dashboard:category_create'), {
            'name': '', 'priority': '2',
        })
        assert resp.status_code == 400


@pytest.mark.django_db
class TestCategoryDelete:
    def test_delete(self, auth_client, categories):
        pk = categories[0].pk
        resp = auth_client.post(reverse('dashboard:category_delete', args=[pk]))
        assert resp.status_code == 302
        assert not Category.objects.filter(pk=pk).exists()


@pytest.mark.django_db
class TestBulkDelete:
    def test_bulk_delete(self, auth_client, categories):
        ids = [c.pk for c in categories]
        resp = auth_client.post(
            reverse('dashboard:categories_bulk_delete'),
            json.dumps({'ids': ids}),
            content_type='application/json',
        )
        data = resp.json()
        assert data['ok']
        assert data['deleted'] == 2
