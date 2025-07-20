document.addEventListener("DOMContentLoaded", () => {
  if (typeof lucide !== "undefined") {
    lucide.createIcons()
  }
})

// DOM elements
const youtubeTab = document.getElementById("youtubeTab")
const uploadTab = document.getElementById("uploadTab")
const youtubeContent = document.getElementById("youtubeContent")
const uploadContent = document.getElementById("uploadContent")
const fileDropZone = document.getElementById("fileDropZone")
const fileInput = document.getElementById("fileInput")
const selectedFile = document.getElementById("selectedFile")
const fileName = document.getElementById("fileName")
const removeFile = document.getElementById("removeFile")
const addWatermark = document.getElementById("addWatermark")
const watermarkInput = document.getElementById("watermarkInput")
const clipForm = document.getElementById("clipForm")
const generateButton = document.getElementById("generateButton")
const statusSection = document.getElementById("statusSection")
const statusText = document.getElementById("statusText")
const progressBar = document.getElementById("progressBar")
const progressText = document.getElementById("progressText")
const resultsSection = document.getElementById("resultsSection")
const errorSection = document.getElementById("errorSection")
const errorMessage = document.getElementById("errorMessage")
const downloadButton = document.getElementById("downloadButton")
const processAnotherButton = document.getElementById("processAnotherButton")

let statusCheckInterval

// Tab switching
youtubeTab.addEventListener("click", () => {
  youtubeTab.className = youtubeTab.className.replace("tab-inactive", "tab-active")
  uploadTab.className = uploadTab.className.replace("tab-active", "tab-inactive")
  youtubeContent.classList.remove("hidden")
  uploadContent.classList.add("hidden")
})

uploadTab.addEventListener("click", () => {
  uploadTab.className = uploadTab.className.replace("tab-inactive", "tab-active")
  youtubeTab.className = youtubeTab.className.replace("tab-active", "tab-inactive")
  uploadContent.classList.remove("hidden")
  youtubeContent.classList.add("hidden")
})

// File upload handling
fileDropZone.addEventListener("click", () => fileInput.click())

fileDropZone.addEventListener("dragover", (e) => {
  e.preventDefault()
  fileDropZone.classList.add("drag-over")
})

fileDropZone.addEventListener("dragleave", () => {
  fileDropZone.classList.remove("drag-over")
})

fileDropZone.addEventListener("drop", (e) => {
  e.preventDefault()
  fileDropZone.classList.remove("drag-over")
  const files = e.dataTransfer.files
  if (files.length > 0) {
    handleFileSelect(files[0])
  }
})

fileInput.addEventListener("change", (e) => {
  if (e.target.files.length > 0) {
    handleFileSelect(e.target.files[0])
  }
})

removeFile.addEventListener("click", () => {
  fileInput.value = ""
  selectedFile.classList.add("hidden")
  fileDropZone.classList.remove("hidden")
})

function handleFileSelect(file) {
  fileName.textContent = file.name
  selectedFile.classList.remove("hidden")
  fileDropZone.classList.add("hidden")
}

// Watermark checkbox handling
addWatermark.addEventListener("change", () => {
  if (addWatermark.checked) {
    watermarkInput.classList.remove("hidden")
  } else {
    watermarkInput.classList.add("hidden")
  }
})

// Form submission
clipForm.addEventListener("submit", (e) => {
  e.preventDefault()
  startProcessing()
})

// Download button
downloadButton.addEventListener("click", () => {
  // Show downloading notification
  showNotification("Preparing download...", "blue")

  // Create a temporary link to trigger download
  const link = document.createElement("a")
  link.href = "/download"
  link.download = "clipah_clips.zip"
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)

  // Show success notification after a delay
  setTimeout(() => {
    showNotification("Download started!", "green")
  }, 1000)
})

// Process Another Video button - Updated with cleanup notification
processAnotherButton.addEventListener("click", () => {
  // Show cleanup notification
  showNotification("🧹 Cleaning up previous files...", "blue")

  // Call reset function
  resetApplication()
})

function showNotification(message, type = "blue") {
  const colors = {
    blue: "bg-blue-600",
    green: "bg-green-600",
    red: "bg-red-600",
  }

  const icons = {
    blue: "info",
    green: "check-circle",
    red: "alert-triangle",
  }

  // Create notification
  const notification = document.createElement("div")
  notification.className = `fixed top-4 right-4 ${colors[type]} text-white px-6 py-3 rounded-lg shadow-lg z-50 flex items-center space-x-2`
  notification.innerHTML = `
    <i data-lucide="${icons[type]}" class="h-5 w-5"></i>
    <span>${message}</span>
  `

  document.body.appendChild(notification)

  // Initialize the icon
  if (typeof lucide !== "undefined") {
    lucide.createIcons()
  }

  // Remove notification after 3 seconds
  setTimeout(() => {
    if (notification.parentNode) {
      notification.parentNode.removeChild(notification)
    }
  }, 3000)
}

function startProcessing() {
  // Get current tab
  const isUploadTab = !uploadContent.classList.contains("hidden")
  
  let formData, isFileUpload = false
  
  if (isUploadTab && fileInput.files.length > 0) {
    // File upload processing
    isFileUpload = true
    formData = new FormData()
    formData.append('video_file', fileInput.files[0])
    formData.append('language', document.getElementById("language").value)
    formData.append('include_subtitles', document.getElementById("generateSubtitles").checked)
    formData.append('include_watermark', document.getElementById("addWatermark").checked)
    formData.append('watermark_text', document.getElementById("watermarkText").value || "@clipah.com")
    formData.append('aspect_ratio', document.querySelector('input[name="aspectRatio"]:checked').value)
  } else {
    // YouTube URL processing (existing)
    const videoUrl = document.getElementById("youtubeUrl").value.trim()
    
    if (!videoUrl) {
      showNotification("Please provide a YouTube URL or upload a video file.", "red")
      return
    }
    
    if (!isValidYouTubeUrl(videoUrl)) {
      showNotification("Please provide a valid YouTube URL.", "red")
      return
    }
    
    formData = JSON.stringify({
      video_url: videoUrl,
      language: document.getElementById("language").value,
      include_subtitles: document.getElementById("generateSubtitles").checked,
      include_watermark: document.getElementById("addWatermark").checked,
      watermark_text: document.getElementById("watermarkText").value || "@clipah.com",
      aspect_ratio: document.querySelector('input[name="aspectRatio"]:checked').value,
    })
  }

  // Validate input
  if (isFileUpload && !fileInput.files.length) {
    showNotification("Please select a video file to upload.", "red")
    return
  }

  // Hide all sections
  hideAllSections()

  // Disable form
  disableForm(true)

  // Show status section
  statusSection.classList.remove("hidden")

  // Prepare fetch options
  const fetchOptions = {
    method: "POST",
    body: formData,
  }

  // Add content type header only for JSON requests
  if (!isFileUpload) {
    fetchOptions.headers = {
      "Content-Type": "application/json",
    }
  }

  // Show appropriate processing message
  if (isFileUpload) {
    showNotification("Uploading and processing video file...", "blue")
    statusText.textContent = "Uploading video file..."
  } else {
    showNotification("Processing YouTube video...", "blue")
  }

  // Send request to backend
  fetch("/process", fetchOptions)
    .then((response) => response.json())
    .then((data) => {
      if (data.error) {
        showError(data.error)
      } else {
        // Start checking status
        startStatusCheck()
      }
    })
    .catch((error) => {
      showError("Failed to start processing: " + error.message)
    })
}

function isValidYouTubeUrl(url) {
  const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\/.+/
  return youtubeRegex.test(url)
}

function startStatusCheck() {
  statusCheckInterval = setInterval(checkStatus, 2000) 
}

function checkStatus() {
  fetch("/status")
    .then((response) => response.json())
    .then((data) => {
      updateStatus(data)

      if (data.status === "completed") {
        clearInterval(statusCheckInterval)
        showResults(data.clips)
      } else if (data.status === "error") {
        clearInterval(statusCheckInterval)
        showError(data.error || "An error occurred during processing")
      }
    })
    .catch((error) => {
      console.error("Error checking status:", error)
    })
}

function updateStatus(data) {
  statusText.textContent = data.message || "Processing..."

  if (data.progress !== undefined) {
    progressBar.style.width = data.progress + "%"
    progressText.textContent = data.progress + "%"
  }
}

function showResults(clips) {
  console.log("showResults called with clips:", clips) 
  hideAllSections()
  generateClipsPreview(clips)
  resultsSection.classList.remove("hidden")
  disableForm(false)

  // Show success notification
  showNotification(`Successfully generated ${clips ? clips.length : 0} clips!`, "green")
}

function generateClipsPreview(clips) {
  const clipsContainer = document.getElementById("clipsContainer")
  const selectedAspectRatio = document.querySelector('input[name="aspectRatio"]:checked').value

  console.log("Generating clips preview with data:", clips) 

  if (!clips || clips.length === 0) {
    clipsContainer.innerHTML = '<p class="text-gray-400 text-center">No clips were generated.</p>'
    return
  }

  // Helper function to get video URL
  function getVideoUrl(clipIndex, clipTitle) {
    const includeWatermark = document.getElementById("addWatermark").checked
    const folderPath = includeWatermark ? "output_clips_final" : "output_clips"
    const safeTitle = clipTitle.replace(/[^a-zA-Z0-9 _]/g, '').replace(/\s+/g, ' ').trim()
    const filename = `${parseInt(clipIndex) + 1}_${safeTitle}${includeWatermark ? '_final' : ''}.mp4`
    return `/${folderPath}/${filename}`
  }

  clipsContainer.innerHTML = clips
    .map(
      (clip, index) => `
        <div class="bg-gray-700/30 rounded-xl p-6 border border-gray-600">
            <div class="grid md:grid-cols-3 gap-6">
                <!-- Video Preview -->
                <div class="space-y-3">
                    <div class="relative ${selectedAspectRatio === "9:16" ? "aspect-[9/16]" : "aspect-video"} bg-gray-800 rounded-lg overflow-hidden group cursor-pointer">
                        <video 
                            class="w-full h-full object-cover" 
                            controls 
                            preload="metadata"
                            data-clip-index="${index}"
                            data-clip-title="${clip.clip_title || `Clip ${index + 1}`}"
                        >
                            <source src="${getVideoUrl(index, clip.clip_title)}" type="video/mp4">
                            <!-- Fallback content when video fails to load -->
                            <div class="w-full h-full bg-gray-800 flex items-center justify-center">
                                <i data-lucide="play-circle" class="h-16 w-16 text-gray-400"></i>
                            </div>
                        </video>
                        <div class="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-2 py-1 rounded">
                            ${calculateDuration(clip.start_time, clip.end_time)}
                        </div>
                    </div>
                    <button class="preview-clip-btn w-full bg-purple-600 hover:bg-purple-700 text-white text-sm font-medium py-2 px-3 rounded-lg transition-colors flex items-center justify-center space-x-2" data-clip-index="${index}" data-clip-title="${clip.clip_title || `Clip ${index + 1}`}">
                        <i data-lucide="play" class="h-4 w-4"></i>
                        <span>Preview Clip</span>
                    </button>
                </div>
                
                <!-- Clip Details -->
                <div class="md:col-span-2 space-y-4">
                    <div>
                        <h5 class="text-lg font-semibold text-white mb-2">${clip.clip_title || `Clip ${index + 1}`}</h5>
                        <div class="flex items-center space-x-4 text-sm text-gray-400 mb-3">
                            <div class="flex items-center space-x-1">
                                <i data-lucide="clock" class="h-4 w-4"></i>
                                <span>${clip.start_time || "N/A"} - ${clip.end_time || "N/A"}</span>
                            </div>
                            <div class="flex items-center space-x-1">
                                <i data-lucide="timer" class="h-4 w-4"></i>
                                <span>${calculateDuration(clip.start_time, clip.end_time)}</span>
                            </div>
                        </div>
                        <p class="text-gray-300 text-sm leading-relaxed">${clip.summary || clip.description || "No summary available"}</p>
                    </div>
                </div>
            </div>
        </div>
    `,
    )
    .join("")

  // Re-initialize icons for the new content
  setTimeout(() => {
    if (typeof lucide !== "undefined") {
      lucide.createIcons()
    }
  }, 100)

  // Add event listeners to preview buttons
  attachPreviewButtonListeners()

  // Add error handling for videos that fail to load
  const videos = clipsContainer.querySelectorAll('video')
  videos.forEach((video, index) => {
    video.addEventListener('error', function() {
      console.log(`Video ${index} failed to load, showing fallback`)
      // Create fallback div
      const fallbackDiv = document.createElement('div')
      fallbackDiv.className = 'w-full h-full bg-gray-800 flex items-center justify-center'
      fallbackDiv.innerHTML = '<i data-lucide="play-circle" class="h-16 w-16 text-gray-400"></i>'
      
      // Replace video with fallback
      this.style.display = 'none'
      this.parentNode.appendChild(fallbackDiv)
      
      // Re-initialize icons
      if (typeof lucide !== "undefined") {
        lucide.createIcons()
      }
    })
    
    video.addEventListener('loadeddata', function() {
      console.log(`Video ${index} loaded successfully`)
    })
  })
}

function calculateDuration(startTime, endTime) {
  try {
    const start = timeToSeconds(startTime)
    const end = timeToSeconds(endTime)
    const duration = end - start
    return Math.round(duration) + "s"
  } catch (e) {
    return "N/A"
  }
}

function attachPreviewButtonListeners() {
  const previewButtons = document.querySelectorAll('.preview-clip-btn')
  previewButtons.forEach(button => {
    button.addEventListener('click', function() {
      const clipIndex = this.getAttribute('data-clip-index')
      const clipTitle = this.getAttribute('data-clip-title')
      
      console.log(`Preview clicked for clip ${clipIndex}: ${clipTitle}`)
      
      // Show notification that preview is being prepared
      showNotification(`Preparing preview for: ${clipTitle}`, "blue")
      
      // Create a video element to preview the clip
      previewClip(clipIndex, clipTitle)
    })
  })
}

function previewClip(clipIndex, clipTitle) {
  // Determine the correct file path based on watermark setting
  const includeWatermark = document.getElementById("addWatermark").checked
  const folderPath = includeWatermark ? "output_clips_final" : "output_clips"
  
  // Generate filename 
  const safeTitle = clipTitle.replace(/[^a-zA-Z0-9 _]/g, '').replace(/\s+/g, ' ').trim()
  const filename = `${parseInt(clipIndex) + 1}_${safeTitle}${includeWatermark ? '_final' : ''}.mp4`
  const videoUrl = `/${folderPath}/${filename}`
  
  console.log(`Attempting to preview video at: ${videoUrl}`)
  
  // Create modal for video preview
  const modal = document.createElement('div')
  modal.className = 'fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50'
  modal.innerHTML = `
    <div class="bg-gray-800 rounded-lg p-6 max-w-4xl max-h-[90vh] overflow-auto">
      <div class="flex justify-between items-center mb-4">
        <h3 class="text-xl font-bold text-white">${clipTitle}</h3>
        <button class="close-modal text-gray-400 hover:text-white text-2xl">&times;</button>
      </div>
      <video controls class="w-full max-h-[70vh]" autoplay>
        <source src="${videoUrl}" type="video/mp4">
        Your browser does not support the video tag.
      </video>
      <p class="text-gray-400 text-sm mt-2">File: ${filename}</p>
    </div>
  `
  
  document.body.appendChild(modal)
  
  // Add close functionality
  const closeButton = modal.querySelector('.close-modal')
  const closeModal = () => {
    document.body.removeChild(modal)
  }
  
  closeButton.addEventListener('click', closeModal)
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeModal()
  })
  
  // Handle video load error
  const video = modal.querySelector('video')
  video.addEventListener('error', () => {
    showNotification(`Could not load video: ${filename}. Check if the file exists.`, "red")
    closeModal()
  })
  
  video.addEventListener('loadstart', () => {
    showNotification(`Loading preview...`, "blue")
  })
  
  video.addEventListener('canplay', () => {
    showNotification(`Preview ready!`, "green")
  })
}

function timeToSeconds(timeStr) {
  try {
    const parts = timeStr.split(":")
    if (parts.length === 3) {
      const [hours, minutes, seconds] = parts
      return Number.parseInt(hours) * 3600 + Number.parseInt(minutes) * 60 + Number.parseFloat(seconds)
    }
    return 0
  } catch (e) {
    return 0
  }
}

function resetApplication() {
  // Clear status check interval
  if (statusCheckInterval) {
    clearInterval(statusCheckInterval)
  }

  // Reset backend (this will trigger the cleanup)
  fetch("/reset", {
    method: "POST",
  })
    .then((response) => response.json())
    .then((data) => {
      console.log("Reset completed:", data)
      showNotification("✅ Cleanup completed successfully!", "green")
    })
    .catch((error) => {
      console.error("Error resetting:", error)
      showNotification("❌ Error during cleanup", "red")
    })

  // Hide all sections
  hideAllSections()

  // Reset form
  clipForm.reset()

  // Reset file upload
  fileInput.value = ""
  selectedFile.classList.add("hidden")
  fileDropZone.classList.remove("hidden")

  // Reset watermark input
  watermarkInput.classList.add("hidden")

  // Reset tabs to YouTube
  youtubeTab.className = youtubeTab.className.replace("tab-inactive", "tab-active")
  uploadTab.className = uploadTab.className.replace("tab-active", "tab-inactive")
  youtubeContent.classList.remove("hidden")
  uploadContent.classList.add("hidden")

  // Reset aspect ratio to 9:16
  document.querySelector('input[name="aspectRatio"][value="9:16"]').checked = true

  // Enable form
  disableForm(false)

  // Reset progress
  progressBar.style.width = "0%"
  progressText.textContent = "0%"

  // Scroll to top
  window.scrollTo({ top: 0, behavior: "smooth" })

  // Re-initialize icons
  setTimeout(() => {
    if (typeof lucide !== "undefined") {
      lucide.createIcons()
    }
  }, 100)
}

function showError(message) {
  // Clear status check interval
  if (statusCheckInterval) {
    clearInterval(statusCheckInterval)
  }

  hideAllSections()
  errorMessage.textContent = message
  errorSection.classList.remove("hidden")
  disableForm(false)

  // Show error notification
  showNotification("Processing failed", "red")
}

function hideAllSections() {
  statusSection.classList.add("hidden")
  resultsSection.classList.add("hidden")
  errorSection.classList.add("hidden")
}

function disableForm(disabled) {
  const formElements = clipForm.querySelectorAll("input, select, button")
  formElements.forEach((element) => {
    element.disabled = disabled
  })

  if (disabled) {
    generateButton.classList.add("opacity-50", "cursor-not-allowed")
  } else {
    generateButton.classList.remove("opacity-50", "cursor-not-allowed")
  }
}

// Re-initialize icons after dynamic content changes
function reinitializeIcons() {
  if (typeof lucide !== "undefined") {
    lucide.createIcons()
  }
}

// Call this after any dynamic content updates
setTimeout(reinitializeIcons, 100)
