import logging

from social.pipeline.social_auth import (
    social_user,
    load_extra_data
)
from social.apps.django_app.default.models import UserSocialAuth

from regluit.core.models import TWITTER, FACEBOOK

logger = logging.getLogger(__name__)


def selectively_associate(backend, uid, user=None, *args, **kwargs):
    """Not using Facebook or Twitter to authenticate a user.
    """
    logger.info('selectively_associate')
    social_auth = UserSocialAuth.get_social_auth(backend.name, uid)
    if backend.name  in ('twitter', 'facebook'):
        # not for authentication
        return {'social_user': social_auth}
    return social_user(backend, uid, user=None, *args, **kwargs)

def facebook_extra_values( user,  extra_data):
    try:
        facebook_id = extra_data.get('id')
        user.profile.facebook_id = facebook_id
        if user.profile.avatar_source is None:
            user.profile.avatar_source = FACEBOOK
        user.profile.save()
        return True
    except Exception,e:
        logger.error(e)
        return False

def twitter_extra_values( user, extra_data):
    try:
        twitter_id = extra_data.get('screen_name')
        profile_image_url = extra_data.get('profile_image_url_https')
        user.profile.twitter_id = twitter_id
        if user.profile.avatar_source is None or user.profile.avatar_source is TWITTER:
            user.profile.pic_url = profile_image_url
        if user.profile.avatar_source is None:
            user.profile.avatar_source = TWITTER
        user.profile.save()
        return True
    except Exception,e:
        logger.error(e)
        return False
        
def deliver_extra_data(backend, details, response, uid, user, social_user=None,
                    *args, **kwargs):
    logger.info('deliver_extra_data')
    pipeline_data = load_extra_data(backend, details, response, uid, user, social_user=None,
                    *args, **kwargs)

    if backend.name is 'twitter':
        twitter_extra_values( user, social_user.extra_data)
    if backend.name is 'facebook':
        facebook_extra_values( user, social_user.extra_data)
        
