function deleteProject(recordId) {
    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 animate__animated animate__fadeIn';
    
    const dialog = document.createElement('div');
    dialog.className = 'glass-card p-8 rounded-xl max-w-md w-full mx-4 animate__animated animate__zoomIn shadow-2xl border border-white/10';
    dialog.innerHTML = `
        <div class="text-center mb-6">
            <div class="relative mb-4">
                <i class='bx bx-trash text-red-400 text-5xl animate-pulse'></i>
                <div class="absolute -top-2 -right-2 w-4 h-4 bg-red-400 rounded-full animate-ping"></div>
            </div>
            <h3 class="text-2xl font-bold text-white mt-4 mb-2">Delete Project</h3>
            <p class="text-gray-300 mt-2 leading-relaxed">Are you sure you want to delete this project? This action cannot be undone.</p>
            <div class="mt-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
                <p class="text-red-300 text-sm">⚠️ Note: This will not delete the associated dev logs.</p>
            </div>
        </div>
        <div class="flex justify-center gap-4 mt-8">
            <button id="cancel-delete" class="bg-slate-700 hover:bg-slate-600 px-6 py-3 text-white rounded-lg font-semibold transition-all duration-300 transform hover:scale-105 shadow-lg">
                Cancel
            </button>
            <button id="confirm-delete" class="bg-gradient-to-r from-red-500 to-red-600 hover:from-red-600 hover:to-red-700 px-6 py-3 text-white rounded-lg font-semibold transition-all duration-300 transform hover:scale-105 shadow-lg">
                Delete Project
            </button>
        </div>
    `;
    
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    
    document.getElementById('cancel-delete').addEventListener('click', () => {
        dialog.classList.replace('animate__zoomIn', 'animate__zoomOut');
        overlay.classList.replace('animate__fadeIn', 'animate__fadeOut');
        setTimeout(() => overlay.remove(), 500);
    });
    
    document.getElementById('confirm-delete').addEventListener('click', () => {
        document.getElementById('confirm-delete').innerHTML = `
            <div class="flex items-center">
                <div class="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-white mr-2"></div>
                Deleting...
            </div>
        `;
        document.getElementById('confirm-delete').disabled = true;
        
        fetch(`/api/projects/${recordId}`, {
            method: 'DELETE'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                dialog.innerHTML = `
                    <div class="text-center mb-6">
                        <div class="relative mb-4">
                            <i class='bx bx-check-circle text-primary-400 text-5xl animate-bounce'></i>
                            <div class="absolute -top-2 -right-2 w-4 h-4 bg-primary-400 rounded-full animate-ping"></div>
                        </div>
                        <h3 class="text-2xl font-bold text-white mt-4">Success!</h3>
                        <p class="text-gray-300 mt-2">Project deleted successfully.</p>
                    </div>
                `;
                
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            } else {
                throw new Error(data.message || 'Failed to delete project');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            dialog.innerHTML = `
                <div class="text-center mb-6">
                    <div class="relative mb-4">
                        <i class='bx bx-error-circle text-red-400 text-5xl animate-pulse'></i>
                        <div class="absolute -top-2 -right-2 w-4 h-4 bg-red-400 rounded-full animate-ping"></div>
                    </div>
                    <h3 class="text-2xl font-bold text-white mt-4">Error</h3>
                    <p class="text-red-300 mt-2">${error.message || 'An error occurred while deleting the project'}</p>
                </div>
                <div class="flex justify-center mt-6">
                    <button id="close-error" class="bg-slate-700 hover:bg-slate-600 px-6 py-3 text-white rounded-lg font-semibold transition-all duration-300 transform hover:scale-105 shadow-lg">
                        Close
                    </button>
                </div>
            `;
            
            document.getElementById('close-error').addEventListener('click', () => {
                dialog.classList.replace('animate__zoomIn', 'animate__zoomOut');
                overlay.classList.replace('animate__fadeIn', 'animate__fadeOut');
                setTimeout(() => overlay.remove(), 500);
            });
        });
    });
}
</script>
{% endblock %}