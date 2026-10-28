import { useEffect, useState } from 'react'

const POST_CATEGORIES = [
  { value: 'feelings', label: 'Feelings' },
  { value: 'playful', label: 'Playful' },
  { value: 'silly', label: 'Silly' },
  { value: 'jokes', label: 'Jokes' },
  { value: 'poetry', label: 'Poetry' },
  { value: 'creative', label: 'Creative' },
]

function PostCard({ post, index, currentUser, onPostUpdate, onPostDelete, onFollowChange }) {
  const [commentsOpen, setCommentsOpen] = useState(false)
  const [comments, setComments] = useState([])
  const [commentsLoaded, setCommentsLoaded] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [commentBusy, setCommentBusy] = useState(false)
  const [reactionBusy, setReactionBusy] = useState('')
  const [interactionError, setInteractionError] = useState('')
  const [reportTarget, setReportTarget] = useState(null)
  const [reportReason, setReportReason] = useState('harassment')
  const [reportDetails, setReportDetails] = useState('')
  const [reportBusy, setReportBusy] = useState(false)
  const [reportNotice, setReportNotice] = useState('')
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [followBusy, setFollowBusy] = useState(false)
  const postedAt = new Date(post.created_at)
  const time = Number.isNaN(postedAt.getTime())
    ? 'just now'
    : postedAt.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })

  async function toggleReaction(reaction) {
    if (!currentUser) {
      setInteractionError('Log in to leave a reaction or comment.')
      document.getElementById('account')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    setReactionBusy(reaction)
    setInteractionError('')
    try {
      const response = await fetch(`/api/posts/${post.id}/reactions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ reaction }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not save that reaction.')
      const myReactions = new Set(post.my_reactions || [])
      result.reacted ? myReactions.add(reaction) : myReactions.delete(reaction)
      onPostUpdate(post.id, {
        reaction_counts: { ...post.reaction_counts, [reaction]: result.total },
        my_reactions: [...myReactions],
      })
    } catch (reactionError) {
      setInteractionError(reactionError.message)
    } finally {
      setReactionBusy('')
    }
  }

  async function toggleFollow() {
    if (!currentUser) {
      setInteractionError('Log in to follow someone.')
      document.getElementById('account')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    setFollowBusy(true)
    setInteractionError('')
    const shouldFollow = !post.is_following
    try {
      const response = await fetch(`/api/users/${post.author_id}/follow`, {
        method: shouldFollow ? 'POST' : 'DELETE',
        credentials: 'include',
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not update who you follow.')
      onFollowChange(post.author_id, shouldFollow)
    } catch (followError) {
      setInteractionError(followError.message)
    } finally {
      setFollowBusy(false)
    }
  }

  async function toggleComments() {
    const willOpen = !commentsOpen
    setCommentsOpen(willOpen)
    setInteractionError('')
    if (!willOpen || commentsLoaded) return
    try {
      const response = await fetch(`/api/posts/${post.id}/comments`)
      if (!response.ok) throw new Error('Could not load comments.')
      setComments(await response.json())
      setCommentsLoaded(true)
    } catch (commentsError) {
      setInteractionError(commentsError.message)
    }
  }

  async function submitComment(event) {
    event.preventDefault()
    const cleanComment = commentText.trim()
    if (!cleanComment || !currentUser) return
    setCommentBusy(true)
    setInteractionError('')
    try {
      const response = await fetch(`/api/posts/${post.id}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ message: cleanComment }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not add your comment.')
      setComments((current) => [...current, result])
      setCommentText('')
      onPostUpdate(post.id, { comment_count: post.comment_count + 1 })
    } catch (commentError) {
      setInteractionError(commentError.message)
    } finally {
      setCommentBusy(false)
    }
  }

  async function deleteOwnComment(comment) {
    if (!window.confirm('Delete this comment?')) return
    setInteractionError('')
    try {
      const response = await fetch(`/api/comments/${comment.id}`, { method: 'DELETE', credentials: 'include' })
      if (!response.ok) throw new Error('Could not delete that comment.')
      setComments((current) => current.filter((item) => item.id !== comment.id))
      onPostUpdate(post.id, { comment_count: Math.max(0, post.comment_count - 1) })
    } catch (deleteError) {
      setInteractionError(deleteError.message)
    }
  }

  async function deleteOwnPost() {
    if (!window.confirm('Delete this post and its comments and reactions?')) return
    setDeleteBusy(true)
    setInteractionError('')
    try {
      const response = await fetch(`/api/posts/${post.id}`, { method: 'DELETE', credentials: 'include' })
      if (!response.ok) throw new Error('Could not delete that post.')
      onPostDelete(post.id)
    } catch (deleteError) {
      setInteractionError(deleteError.message)
      setDeleteBusy(false)
    }
  }

  async function submitReport(event) {
    event.preventDefault()
    if (!currentUser) return
    setReportBusy(true)
    setReportNotice('')
    setInteractionError('')
    try {
      const response = await fetch('/api/reports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ ...reportTarget, reason: reportReason, details: reportDetails.trim() }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not send that report.')
      setReportTarget(null)
      setReportDetails('')
      setReportNotice('Thanks. Your report is in the review queue.')
    } catch (reportError) {
      setInteractionError(reportError.message)
    } finally {
      setReportBusy(false)
    }
  }

  function openReport(contentType, contentId) {
    if (!currentUser) {
      setInteractionError('Log in to report content.')
      document.getElementById('account')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    setReportNotice('')
    setReportReason('harassment')
    setReportTarget({ content_type: contentType, content_id: contentId })
  }

  return (
    <article className={`post-card post-tone-${index % 3}`}>
      <p className="post-message">{post.message}</p>
      {post.image_url && <img className="post-image" src={post.image_url} alt={`Photo shared by ${post.author}`} loading="lazy" />}
      <span className="post-category">{POST_CATEGORIES.find((item) => item.value === post.category)?.label || 'Feelings'}</span>
      <div className="post-byline">
        <span className="avatar" aria-hidden="true">{post.author.slice(0, 1).toUpperCase()}</span>
        <span className="post-author">{post.author}</span>
        <span className="byline-dot" aria-hidden="true">·</span>
        <time dateTime={post.created_at}>{time}</time>
        {post.author_id && currentUser?.id !== post.author_id && (
          <button className={`follow-button ${post.is_following ? 'following' : ''}`} type="button" onClick={toggleFollow} disabled={followBusy}>
            {followBusy ? '…' : post.is_following ? 'Following' : 'Follow'}
          </button>
        )}
      </div>
      <div className="post-actions" aria-label="Post reactions and comments">
        {[
          { key: 'heart', emoji: '❤️', label: 'Relate' },
          { key: 'laugh', emoji: '😂', label: 'Funny' },
          { key: 'hug', emoji: '🫂', label: 'Sending a hug' },
        ].map(({ key, emoji, label }) => (
          <button
            className={`reaction-button ${(post.my_reactions || []).includes(key) ? 'is-reacted' : ''}`}
            key={key}
            type="button"
            title={label}
            aria-label={`${label}, ${post.reaction_counts?.[key] || 0} reactions`}
            aria-pressed={(post.my_reactions || []).includes(key)}
            disabled={reactionBusy === key}
            onClick={() => toggleReaction(key)}
          >
            <span aria-hidden="true">{emoji}</span><span>{post.reaction_counts?.[key] || 0}</span>
          </button>
        ))}
        <button className={`comments-toggle ${commentsOpen ? 'is-open' : ''}`} type="button" onClick={toggleComments} aria-expanded={commentsOpen}>
          {commentsOpen ? 'Close replies' : 'Comment'} <span>{post.comment_count || 0}</span>
        </button>
        {post.can_delete && <button className="post-management-button" type="button" disabled={deleteBusy} onClick={deleteOwnPost}>{deleteBusy ? 'Deleting…' : 'Delete'}</button>}
        <button className="post-management-button" type="button" onClick={() => openReport('post', post.id)}>Report</button>
      </div>
      {reportTarget && (
        <form className="report-form" onSubmit={submitReport}>
          <div className="report-form-heading"><strong>Tell us what’s wrong</strong><button className="text-button" type="button" onClick={() => setReportTarget(null)}>Cancel</button></div>
          <label htmlFor={`report-reason-${post.id}`}>Reason
            <select id={`report-reason-${post.id}`} value={reportReason} onChange={(event) => setReportReason(event.target.value)}>
              <option value="harassment">Harassment or bullying</option>
              <option value="private_information">Someone’s private information</option>
              <option value="spam">Spam</option>
              <option value="other">Something else</option>
            </select>
          </label>
          <label htmlFor={`report-details-${post.id}`}>A little more detail (optional)
            <textarea id={`report-details-${post.id}`} value={reportDetails} onChange={(event) => setReportDetails(event.target.value)} maxLength={300} placeholder="Help us understand what happened…" />
          </label>
          <button className="report-submit" type="submit" disabled={reportBusy}>{reportBusy ? 'Sending…' : 'Send report'}</button>
        </form>
      )}
      {reportNotice && <p className="report-notice" role="status">{reportNotice}</p>}
      {interactionError && <p className="interaction-error" role="status">{interactionError}</p>}
      {commentsOpen && (
        <section className="comments-panel" aria-label="Comments">
          {comments.length ? (
            <div className="comment-list">
              {comments.map((comment) => (
                <div className="comment" key={comment.id}>
                  <div className="comment-heading"><span className="comment-author">@{comment.author}</span><span className="comment-controls">
                    {comment.can_delete && <button className="comment-action" type="button" onClick={() => deleteOwnComment(comment)}>Delete</button>}
                    <button className="comment-action" type="button" onClick={() => openReport('comment', comment.id)}>Report</button>
                  </span></div>
                  <p>{comment.message}</p>
                </div>
              ))}
            </div>
          ) : <p className="no-comments">No replies yet. You could leave the first.</p>}
          {currentUser ? (
            <form className="comment-form" onSubmit={submitComment}>
              <label className="visually-hidden" htmlFor={`comment-${post.id}`}>Write a comment</label>
              <input id={`comment-${post.id}`} value={commentText} onChange={(event) => setCommentText(event.target.value)} maxLength={300} placeholder="Add a kind thought…" required />
              <button type="submit" disabled={commentBusy || !commentText.trim()}>{commentBusy ? 'Sending…' : 'Reply'}</button>
            </form>
          ) : <p className="login-to-comment">Log in to join the conversation.</p>}
        </section>
      )}
    </article>
  )
}

function ModeratorQueue() {
  const [reports, setReports] = useState([])
  const [activity, setActivity] = useState([])
  const [open, setOpen] = useState(false)
  const [activityOpen, setActivityOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [activityLoading, setActivityLoading] = useState(false)
  const [error, setError] = useState('')
  const [activityError, setActivityError] = useState('')

  async function loadReports() {
    setLoading(true)
    setError('')
    try {
      const response = await fetch('/api/moderation/reports', { credentials: 'include' })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not load reports.')
      setReports(result)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }

  async function toggleQueue() {
    const willOpen = !open
    setOpen(willOpen)
    if (willOpen) await loadReports()
  }

  async function loadActivity() {
    setActivityLoading(true)
    setActivityError('')
    try {
      const response = await fetch('/api/moderation/activity', { credentials: 'include' })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not load account activity.')
      setActivity(result)
    } catch (loadError) {
      setActivityError(loadError.message)
    } finally {
      setActivityLoading(false)
    }
  }

  async function toggleActivity() {
    const willOpen = !activityOpen
    setActivityOpen(willOpen)
    if (willOpen) await loadActivity()
  }

  async function resolveReport(report, action) {
    if (action === 'remove' && !window.confirm('Remove this reported content from the feed?')) return
    setError('')
    try {
      const response = await fetch(`/api/moderation/reports/${report.id}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ action }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not resolve that report.')
      if (action === 'remove') {
        window.location.reload()
        return
      }
      setReports((current) => current.filter((item) => item.id !== report.id))
    } catch (resolveError) {
      setError(resolveError.message)
    }
  }

  return (
    <section className="moderator-queue">
      <div className="moderator-tools">
        <button className="moderator-toggle" type="button" onClick={toggleQueue} aria-expanded={open}>
          {open ? 'Close report queue' : `Review reports${reports.length ? ` · ${reports.length}` : ''}`}
        </button>
        <button className="moderator-toggle" type="button" onClick={toggleActivity} aria-expanded={activityOpen}>
          {activityOpen ? 'Close account activity' : 'Account activity'}
        </button>
      </div>
      {open && <div className="moderator-panel">
        <div className="moderator-heading"><h2>Reports to review</h2><button className="text-button" type="button" onClick={loadReports} disabled={loading}>Refresh</button></div>
        {error && <p className="error-message" role="alert">{error}</p>}
        {loading ? <p className="feed-message">Loading reports…</p> : reports.length ? reports.map((report) => (
          <article className="report-card" key={report.id}>
            <p><strong>{report.content_type} by @{report.content_author}</strong> · reported by @{report.reporter}</p>
            <p className="report-reason">{report.reason.replaceAll('_', ' ')} · {report.created_at.slice(0, 16).replace('T', ' ')}</p>
            <blockquote>{report.content_snapshot}</blockquote>
            {report.image_url && <img className="report-image" src={report.image_url} alt="Image attached to reported content" />}
            {report.details && <p className="report-details">Reporter’s note: {report.details}</p>}
            <div className="report-actions"><button type="button" className="text-button" onClick={() => resolveReport(report, 'dismiss')}>Dismiss report</button><button type="button" className="remove-content-button" onClick={() => resolveReport(report, 'remove')}>Remove content</button></div>
          </article>
        )) : !error && <p className="feed-message">No reports waiting. Nice and quiet.</p>}
      </div>}
      {activityOpen && <div className="moderator-panel activity-panel">
        <div className="moderator-heading"><div><h2>Account activity</h2><p className="activity-caption">Only moderators can see this. Signed-in accounts check in about once a minute while the site is open.</p></div><button className="text-button" type="button" onClick={loadActivity} disabled={activityLoading}>Refresh</button></div>
        {activityError && <p className="error-message" role="alert">{activityError}</p>}
        {activityLoading ? <p className="feed-message">Loading activity…</p> : activity.length ? <div className="activity-list">
          {activity.map((user) => <div className="activity-row" key={user.id}>
            <strong>@{user.username}</strong>
            <span className={user.is_online ? 'activity-status is-online' : 'activity-status'}>{user.is_online ? 'Online' : user.last_seen_at ? `Last active ${new Date(user.last_seen_at).toLocaleString()}` : 'Not active yet'}</span>
          </div>)}
        </div> : !activityError && <p className="feed-message">No accounts yet.</p>}
      </div>}
    </section>
  )
}

export default function App() {
  const [posts, setPosts] = useState([])
  const [message, setMessage] = useState('')
  const [draftCategory, setDraftCategory] = useState('feelings')
  const [imageFile, setImageFile] = useState(null)
  const [imagePreview, setImagePreview] = useState('')
  const [currentUser, setCurrentUser] = useState(null)
  const [checkingSession, setCheckingSession] = useState(true)
  const [authMode, setAuthMode] = useState('signup')
  const [authUsername, setAuthUsername] = useState('')
  const [authPassword, setAuthPassword] = useState('')
  const [authPasswordConfirm, setAuthPasswordConfirm] = useState('')
  const [authBusy, setAuthBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [authError, setAuthError] = useState('')
  const [activeCategory, setActiveCategory] = useState('all')
  const [feedMode, setFeedMode] = useState('all')

  async function refreshPosts(category = activeCategory, mode = feedMode) {
    const params = new URLSearchParams({ category, feed: mode })
    const response = await fetch(`/api/posts?${params}`, { credentials: 'include' })
    if (!response.ok) throw new Error('Could not load the feed.')
    if (response.ok) setPosts(await response.json())
  }

  async function changeFeed(category = activeCategory, mode = feedMode) {
    setActiveCategory(category)
    setFeedMode(mode)
    setLoading(true)
    setError('')
    try {
      await refreshPosts(category, mode)
    } catch {
      setError('The feed could not load. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!imagePreview) return undefined
    return () => URL.revokeObjectURL(imagePreview)
  }, [imagePreview])

  function handleImageSelect(event) {
    const file = event.target.files?.[0]
    if (!file) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setError('Choose a JPG, PNG, or WebP image.')
      event.target.value = ''
      return
    }
    if (file.size > 8 * 1024 * 1024) {
      setError('Choose an image smaller than 8 MB.')
      event.target.value = ''
      return
    }
    setError('')
    setImageFile(file)
    setImagePreview(URL.createObjectURL(file))
  }

  useEffect(() => {
    refreshPosts()
      .catch(() => setError('The feed could not load. Make sure the app server is running and try again.'))
      .finally(() => setLoading(false))

    fetch('/api/auth/me', { credentials: 'include' })
      .then((response) => response.json())
      .then((session) => setCurrentUser(session.username ? session : null))
      .catch(() => setCurrentUser(null))
      .finally(() => setCheckingSession(false))
  }, [])

  useEffect(() => {
    if (!currentUser) return undefined

    const checkIn = () => {
      if (document.visibilityState !== 'visible') return
      fetch('/api/presence', { method: 'POST', credentials: 'include' }).catch(() => {})
    }
    checkIn()
    const interval = window.setInterval(checkIn, 60_000)
    document.addEventListener('visibilitychange', checkIn)
    return () => {
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', checkIn)
    }
  }, [currentUser])

  async function handleAuthSubmit(event) {
    event.preventDefault()
    setAuthError('')
    if (authMode === 'signup' && authPassword !== authPasswordConfirm) {
      setAuthError('Those passwords don’t match yet.')
      return
    }

    setAuthBusy(true)
    try {
      const response = await fetch(`/api/auth/${authMode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username: authUsername.trim(), password: authPassword }),
      })
      const result = await response.json()
      if (!response.ok) {
        const detail = Array.isArray(result.detail) ? result.detail[0]?.msg : result.detail
        throw new Error(detail || 'We couldn’t sign you in. Please try again.')
      }
      setCurrentUser(result)
      await refreshPosts()
      setAuthPassword('')
      setAuthPasswordConfirm('')
    } catch (authSubmitError) {
      setAuthError(authSubmitError.message || 'We couldn’t sign you in. Please try again.')
    } finally {
      setAuthBusy(false)
    }
  }

  async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
    setCurrentUser(null)
    setAuthMode('login')
    await refreshPosts()
  }

  function updatePost(postId, changes) {
    setPosts((currentPosts) => currentPosts.map((post) => post.id === postId ? { ...post, ...changes } : post))
  }

  function removePost(postId) {
    setPosts((currentPosts) => currentPosts.filter((post) => post.id !== postId))
  }

  function updateFollowing(userId, following) {
    setPosts((currentPosts) => currentPosts
      .filter((post) => !(feedMode === 'following' && !following && post.author_id === userId))
      .map((post) => post.author_id === userId ? { ...post, is_following: following } : post))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    const cleanMessage = message.trim()
    if (!cleanMessage) {
      setError('Write a little something before sharing.')
      return
    }

    setSaving(true)
    setError('')
    try {
      const formData = new FormData()
      formData.append('message', cleanMessage)
      formData.append('category', draftCategory)
      if (imageFile) formData.append('image', imageFile)
      const response = await fetch('/api/posts', {
        method: 'POST',
        credentials: 'include',
        body: formData,
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Your post could not be shared. Please try again.')
      if (feedMode === 'all' && (activeCategory === 'all' || activeCategory === draftCategory)) {
        setPosts((currentPosts) => [result, ...currentPosts])
      }
      setMessage('')
      setImageFile(null)
      setImagePreview('')
    } catch (submitError) {
      setError(submitError.message || 'Your post could not be shared. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top"><span className="brand-mark" aria-hidden="true">“</span> Say It Anyway</a>
        <span className="topbar-note">A little room to be human</span>
      </header>

      <main id="top">
        <section className="intro">
          <p className="eyebrow">No perfect words required</p>
          <h1>Some things just<br /><em>need somewhere to go.</em></h1>
          <p className="intro-copy">Got a silly bit of banter? Something funny happen today? A weird coincidence you can’t stop thinking about? Want to rant about someone, share a few lines of poetry, drop a confession 😉, or post that random 2 a.m. thought? Maybe you’re wondering, “Why do people behave like thatttt?” or yelling, “Life is crazyyyyy!” 😵‍💫 Whatever it is, leave it here. No need to make it perfect or overthink it—share your kind of crazy. 🤭</p>
        </section>

        {currentUser?.is_moderator && <ModeratorQueue />}

        <div className="content-grid">
          <section className="share-column" id="account" aria-labelledby="share-title">
            {checkingSession ? (
              <p className="feed-message">One moment…</p>
            ) : currentUser ? (
              <>
                <div className="section-heading logged-in-heading">
                  <div><h2 id="share-title">Drop it here.</h2><p>Posting as <strong>@{currentUser.username}</strong></p></div>
                  <button className="text-button" type="button" onClick={handleLogout}>Log out</button>
                </div>
                <section className="composer" aria-label="Write a thought">
                  <form onSubmit={handleSubmit}>
                    <label className="visually-hidden" htmlFor="message">Your thought or feeling</label>
                    <textarea id="message" value={message} onChange={(event) => setMessage(event.target.value)} maxLength={500} placeholder="Whatever it is, it can start here…" required />
                    <div className="composer-options">
                      <label className="category-select-label" htmlFor="draft-category">Put it under
                        <select id="draft-category" value={draftCategory} onChange={(event) => setDraftCategory(event.target.value)}>
                          {POST_CATEGORIES.map((category) => <option key={category.value} value={category.value}>{category.label}</option>)}
                        </select>
                      </label>
                      <label className="photo-picker">{imageFile ? 'Change photo' : 'Add a photo'}
                        <input type="file" accept="image/jpeg,image/png,image/webp" onChange={handleImageSelect} />
                      </label>
                    </div>
                    {imagePreview && <div className="image-preview"><img src={imagePreview} alt="Selected photo preview" /><button type="button" className="text-button" onClick={() => { setImageFile(null); setImagePreview('') }}>Remove photo</button></div>}
                    <div className="form-footer">
                      <span className="character-count">{message.length}/500</span>
                      <button className="share-button" type="submit" disabled={saving}>
                        {saving ? 'Sharing…' : 'Share'} <span aria-hidden="true">↗</span>
                      </button>
                    </div>
                  </form>
                  {error && <p className="error-message" role="alert">{error}</p>}
                  <p className="privacy-note">Your username appears with your post.</p>
                </section>
              </>
            ) : (
              <section className="auth-card" aria-labelledby="share-title">
                <div className="section-heading auth-heading">
                  <p className="eyebrow">{authMode === 'signup' ? 'Make yourself at home' : 'Good to see you again'}</p>
                  <h2 id="share-title">{authMode === 'signup' ? 'Join the room.' : 'Welcome back.'}</h2>
                  <p>{authMode === 'signup' ? 'Choose a username. It does not need to be your real name—we don’t ask for it.' : 'Log in with your username and password to share a thought.'}</p>
                </div>
                <form className="auth-form" onSubmit={handleAuthSubmit}>
                  <label htmlFor="auth-username">Username
                    <input id="auth-username" autoComplete="username" minLength={3} maxLength={20} value={authUsername} onChange={(event) => setAuthUsername(event.target.value)} placeholder="letters, numbers, or _" required />
                  </label>
                  <label htmlFor="auth-password">Password
                    <input id="auth-password" type="password" autoComplete={authMode === 'signup' ? 'new-password' : 'current-password'} minLength={authMode === 'signup' ? 8 : undefined} maxLength={128} value={authPassword} onChange={(event) => setAuthPassword(event.target.value)} placeholder={authMode === 'signup' ? 'At least 8 characters' : 'Your password'} required />
                  </label>
                  {authMode === 'signup' && <label htmlFor="auth-password-confirm">Confirm password
                    <input id="auth-password-confirm" type="password" autoComplete="new-password" minLength={8} maxLength={128} value={authPasswordConfirm} onChange={(event) => setAuthPasswordConfirm(event.target.value)} placeholder="Type it once more" required />
                  </label>}
                  {authError && <p className="error-message" role="alert">{authError}</p>}
                  <button className="share-button auth-submit" type="submit" disabled={authBusy}>{authBusy ? 'One moment…' : authMode === 'signup' ? 'Create username' : 'Log in'} <span aria-hidden="true">↗</span></button>
                </form>
                <p className="auth-switch">
                  {authMode === 'signup' ? 'Already have a username?' : 'New around here?'}{' '}
                  <button className="text-button" type="button" onClick={() => { setAuthMode(authMode === 'signup' ? 'login' : 'signup'); setAuthError('') }}>{authMode === 'signup' ? 'Log in' : 'Create one'}</button>
                </p>
              </section>
            )}
          </section>

          <section className="feed-section" aria-labelledby="feed-title">
            <div className="feed-heading">
              <div><p className="eyebrow">Little thoughts, big feelings</p><h2 id="feed-title">From all of us</h2></div>
              <span>{posts.length.toString().padStart(2, '0')}</span>
            </div>
            <div className="feed-modes" role="group" aria-label="Choose feed">
              <button className={feedMode === 'all' ? 'active' : ''} type="button" onClick={() => changeFeed(activeCategory, 'all')}>Everyone</button>
              <button className={feedMode === 'following' ? 'active' : ''} type="button" onClick={() => currentUser ? changeFeed(activeCategory, 'following') : document.getElementById('account')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>Following</button>
            </div>
            <div className="category-tabs" role="group" aria-label="Filter by section">
              <button className={activeCategory === 'all' ? 'active' : ''} type="button" onClick={() => changeFeed('all', feedMode)}>All</button>
              {POST_CATEGORIES.map((category) => <button className={activeCategory === category.value ? 'active' : ''} key={category.value} type="button" onClick={() => changeFeed(category.value, feedMode)}>{category.label}</button>)}
            </div>
            {loading ? (
              <p className="feed-message">Loading thoughts…</p>
            ) : posts.length ? (
              <div className="post-list">{posts.map((post, index) => <PostCard key={post.id} post={post} index={index} currentUser={currentUser} onPostUpdate={updatePost} onPostDelete={removePost} onFollowChange={updateFollowing} />)}</div>
            ) : (
              <div className="empty-card">
                <p>{feedMode === 'following' ? 'Your following feed is quiet.' : 'It’s quiet here for now.'}</p>
                <span>{feedMode === 'following' ? 'Follow a few people from Everyone to see their thoughts here.' : 'Whenever you’re ready, leave the first thought.'}</span>
              </div>
            )}
          </section>
        </div>
      </main>

      <footer className="footer">A little room for all of us <span aria-hidden="true">♡</span></footer>
    </div>
  )
}
