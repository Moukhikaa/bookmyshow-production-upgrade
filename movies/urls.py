from django.urls import path
from . import views
urlpatterns=[
    path('',views.movie_list,name='movie_list'),
    path('<int:movie_id>/', views.movie_detail, name='movie_detail'),
    path('<int:movie_id>/theaters',views.theater_list,name='theater_list'),
    path('theater/<int:theater_id>/seats/book/',views.book_seats,name='book_seats'),
    path('payment/<uuid:reservation_id>/', views.payment_checkout, name='payment_checkout'),
    path('payment/success/', views.payment_success, name='payment_success'),
    path('payment/<uuid:reservation_id>/cancel/', views.payment_cancel, name='payment_cancel'),
    path('payment/webhook/razorpay/', views.razorpay_webhook, name='razorpay_webhook'),
    path('admin/analytics/', views.analytics_dashboard, name='analytics_dashboard'),
]
