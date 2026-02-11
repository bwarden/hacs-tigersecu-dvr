#Still images lock/concurrency/caching/rate-limiting algorithm

When attempting to get an image from a camera:
1. Attempt to take a lock, non-blocking. If we get it, wait until the `_backoff_until time` has been reached, then update the `_backoff_until_time` to 10 seconds in the future, and spawn an async fetch for a new image, using the config variable `still_image_timeout`.
  1. If the fetch does not complete within 10 seconds, let it continue, but return the last cached image.
  1. If the fetch fails, release the lock, update the `_backoff_until_time` to 10 seconds in the future, and return the last cached image.
  1. If the fetch completes, update the last cached image, update the `_backoff_until_time` to 10 seconds in the future, release the lock, and return the last cached image.
1. If we don't get the lock, wait up to 10 seconds for it to be freed, then return the last cached image.

Make sure the solution satisfies coherency and cannot result in a deadlock.
